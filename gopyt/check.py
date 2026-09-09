"""GoPyT v0 semantic analysis.

Produces the typed, monomorphized program that gopyt/emit.py lowers to
bytecode. Every rule here cites a locked document; nothing is invented.
"""

from __future__ import annotations

import os
import math
import re
from dataclasses import dataclass, field, fields

from gopyt import types as T
from gopyt.transaction import guarded, guard
from gopyt.files import regular_file
from gopyt.ast_nodes import *
from gopyt.decls import AgentInfo, ProvideInfo, RouteInfo, Sig, TraitInfo, TypeInfo
from gopyt.diag import CompileError, Diag
from gopyt.fmt import fmt_module
from gopyt.manifest import (
    Manifest,
    lock_text,
    manifest_text,
    package_digest,
    parse_manifest,
)
from gopyt.parser import EFFECTS, parse_module
from gopyt.stdlib_src import STDLIB_SOURCE
from gopyt.types import (
    BOOL,
    F64,
    I64,
    NONE,
    STR,
    UNIT,
    ListT,
    MapT,
    Nom,
    NoneTy,
    Opt,
    Prim,
    Ty,
    Union,
    Var,
    assignable,
    has_var,
    members_of,
    subst,
    supports_eq,
    unify,
)

STD_PREFIXES = ("core", "net", "data", "store")
SECRET = Nom("core.secret.Secret")
JSON_BUILTINS = ("bool", "i32", "i64", "u32", "u64", "str", "unit")
FROM_STR_BUILTINS = ("i64", "bool", "str")
# Names that may never be JSON or logged (docs/security.md).
NON_JSON = ("f64", "bytes")


def err(code: int, file: str | None, line: int | None, repair: str = "") -> CompileError:
    return CompileError(Diag(code, file, line, 0, repair=repair))


def _path_module(rel: str) -> str:
    rel = rel.replace("\\", "/")
    if rel.endswith(".gopyt"):
        rel = rel[: -len(".gopyt")]
    return rel.replace("/", ".")


def inst_key(symbol: str, targs: tuple[Ty, ...]) -> str:
    if not targs:
        return symbol
    return symbol + "[" + ", ".join(a.key for a in targs) + "]"


# ---------------------------------------------------------------- resolutions


@dataclass
class CallRes:
    symbol: str
    targs: tuple[Ty, ...]
    kind: str  # fn | task
    effects: frozenset[str]
    ret: Ty

    @property
    def key(self) -> str:
        return inst_key(self.symbol, self.targs)


@dataclass
class FieldRes:
    index: int


@dataclass
class ConstructRes:
    nom: Nom
    kind: str  # record | enum
    variant: int = -1
    order: list[int] = field(default_factory=list)  # source field order -> decl index


@dataclass
class ArmRes:
    kind: str  # enum | union | optional
    tag: int  # variant index, type id slot, or 0/1 for some/none
    nom: Nom | None
    binds: list[tuple[int, int]] = field(default_factory=list)  # (slot, field index)
    scalar: str | None = None  # union arm naming a scalar builtin member (S7)

    @property
    def member(self) -> Ty | None:
        if self.scalar is not None:
            return Prim(self.scalar)
        return self.nom


@dataclass
class MatchRes:
    mode: str  # enum | union | optional
    scrut: Ty
    arms: list[ArmRes] = field(default_factory=list)


@dataclass
class ParArmRes:
    symbol: str
    captures: list[object]  # LocalRes or CaptureRes, in capture-record order
    cap_names: list[str]
    cap_types: list[Ty]


@dataclass
class ParallelRes:
    arms: list[ParArmRes]
    elem: Ty


@dataclass
class FuncCheck:
    symbol: str
    targs: tuple[Ty, ...]
    kind: str
    module: str
    file: str
    params: list[tuple[str, Ty]]
    ret: Ty
    effects: frozenset[str]
    contracts: list = field(default_factory=list)
    body: Block | None = None
    local_types: list[Ty] = field(default_factory=list)
    ty: dict[int, Ty] = field(default_factory=dict)
    res: dict[int, object] = field(default_factory=dict)
    slot: dict[int, int] = field(default_factory=dict)
    for_list: dict[int, int] = field(default_factory=dict)
    for_idx: dict[int, int] = field(default_factory=dict)
    result_slot: int = -1
    parent: str = ""  # owning function for compiler arms

    @property
    def key(self) -> str:
        return inst_key(self.symbol, self.targs)

    @property
    def arity(self) -> int:
        return len(self.params)


@dataclass
class Program:
    funcs: dict[str, FuncCheck] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    types: dict[str, TypeInfo] = field(default_factory=dict)
    noms: dict[str, Nom] = field(default_factory=dict)
    routes: list[tuple[str, str, str, str]] = field(default_factory=list)
    egress: list[tuple[str, str]] = field(default_factory=list)
    # module -> (max, timeout_ms, reservoir) from an agent's `evolve { }` (S31)
    evolve: dict[str, tuple[int, int, int]] = field(default_factory=dict)
    root_module_prefix: str = ""


@dataclass
class Pkg:
    root: str
    manifest: Manifest | None = None
    spec: dict[str, Module] = field(default_factory=dict)
    impl: dict[str, Module] = field(default_factory=dict)
    tests: dict[str, Module] = field(default_factory=dict)
    stdlib: dict[str, Module] = field(default_factory=dict)
    dep_specs: dict[str, Module] = field(default_factory=dict)
    dep_impls: dict[str, Module] = field(default_factory=dict)
    packages: list[tuple[str, str, str, str]] = field(default_factory=list)
    types: dict[str, TypeInfo] = field(default_factory=dict)
    # lockfile.md: imports are direct, so who owns a module and what each
    # package may import has to survive loading.
    module_owner: dict[str, str] = field(default_factory=dict)
    package_deps: dict[str, set[str]] = field(default_factory=dict)


# ---------------------------------------------------------------- loading


def _read_module(full: str, file_rel: str, role: str, expect: str) -> Module:
    with regular_file(os.path.dirname(full), os.path.basename(full)) as fh:
        raw = fh.read()
    try:
        src = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise err(3, file_rel, 1)
    mod = parse_module(src, file_rel, role)
    if mod.path != expect:
        raise err(12, file_rel, 1, repair=f"module {expect}\n")
    return mod


def _walk_dir(root: str, folder: str, role: str, prefix: str = "", file_prefix: str = "") -> list[Module]:
    out: list[Module] = []
    base = os.path.join(root, folder)
    if not os.path.isdir(base):
        return out
    for dirpath, dirnames, files in os.walk(base):
        dirnames.sort()
        for fn in sorted(files):
            if not fn.endswith(".gopyt"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, base).replace(os.sep, "/")
            file_rel = f"{file_prefix}{folder}/{rel}"
            expect = prefix + _path_module(rel)
            out.append(_read_module(full, file_rel, role, expect))
    return out


def load_stdlib() -> dict[str, Module]:
    mods: dict[str, Module] = {}
    for name, src in STDLIB_SOURCE.items():
        rel = "stdlib/" + name.replace(".", "/") + ".gopyt"
        mods[name] = parse_module(src, rel, "stdlib")
    return mods


@guarded
def load_package(root: str) -> Pkg:
    from gopyt.manifest import validate_source_tree

    validate_source_tree(root)
    man = parse_manifest(root)
    pkg = Pkg(root=root, manifest=man)
    pkg.stdlib = load_stdlib()
    pkg.package_deps[man.name] = set(man.deps)
    for mod in _walk_dir(root, "spec", "spec"):
        pkg.spec[mod.path] = mod
        pkg.module_owner[mod.path] = man.name
    for mod in _walk_dir(root, "impl", "impl"):
        pkg.impl[mod.path] = mod
        pkg.module_owner.setdefault(mod.path, man.name)
    for mod in _walk_dir(root, "test", "test", prefix="test."):
        pkg.tests[mod.path] = mod
        pkg.module_owner[mod.path] = man.name
    expected = {m.file: m.source.encode("utf-8") for group in (pkg.spec, pkg.impl, pkg.tests) for m in group.values()}
    expected["gopyt.toml"] = manifest_text(man).encode("utf-8")
    pkg.packages = [(man.name, man.version, ".", package_digest(root, expected))]
    root_real = os.path.realpath(root)
    _load_deps(pkg, man, root, seen={root_real: man.name}, stack=(root_real,))
    pkg.packages[1:] = sorted(pkg.packages[1:], key=lambda entry: entry[0].encode("utf-8"))
    return pkg


def _load_deps(
    pkg: Pkg,
    man: Manifest,
    root: str,
    seen: dict[str, str],
    stack: tuple[str, ...] = (),
) -> None:
    """docs/lockfile.md graph rules: E042 cycle, E043 missing, E044 name, E045 dup."""
    for key in sorted(man.deps, key=lambda k: k.encode("utf-8")):
        dep_dir = os.path.normpath(os.path.join(root, man.deps[key]))
        from gopyt.manifest import validate_source_tree

        validate_source_tree(dep_dir)
        if not os.path.isdir(dep_dir):
            raise err(43, "gopyt.toml", 1)
        real = os.path.realpath(dep_dir)
        if real in stack:
            raise err(42, "gopyt.toml", 1)
        if real in seen:
            if seen[real] != key:
                raise err(44, "gopyt.toml", 1)
            continue
        with guard(dep_dir):
            dep_man = parse_manifest(dep_dir)
            if dep_man.name != key:
                raise err(44, "gopyt.toml", 1)
            if any(p[0] == key for p in pkg.packages):
                # Same name, different canonical path, anywhere in the graph.
                raise err(45, "gopyt.toml", 1)
            seen[real] = key
            pkg.package_deps[dep_man.name] = set(dep_man.deps)
            file_prefix = os.path.relpath(dep_dir, pkg.root).replace(os.sep, "/") + "/"
            for mod in _walk_dir(dep_dir, "spec", "spec", file_prefix=file_prefix):
                if not mod.path.split(".")[0] == key:
                    raise err(44, mod.file, 1)
                pkg.dep_specs[mod.path] = mod
                pkg.module_owner[mod.path] = dep_man.name
            for mod in _walk_dir(dep_dir, "impl", "impl", file_prefix=file_prefix):
                pkg.dep_impls[mod.path] = mod
                pkg.module_owner.setdefault(mod.path, dep_man.name)
            relative = os.path.relpath(dep_dir, pkg.root).replace(os.sep, "/")
            expected = {m.file[len(file_prefix):]: m.source.encode("utf-8")
                        for group in (pkg.dep_specs, pkg.dep_impls) for m in group.values()
                        if m.file.startswith(file_prefix)}
            # Dependency tests are not imported, but their exact bytes remain
            # part of the dependency digest.
            for path, dirs, files in os.walk(os.path.join(dep_dir, "test")):
                for filename in files:
                    if filename.endswith(".gopyt"):
                        rel = os.path.relpath(os.path.join(path, filename), dep_dir)
                        with regular_file(dep_dir, rel) as stream:
                            expected[rel] = stream.read()
            expected["gopyt.toml"] = manifest_text(dep_man).encode("utf-8")
            pkg.packages.append((dep_man.name, dep_man.version, relative, package_digest(dep_dir, expected)))
            _load_deps(pkg, dep_man, dep_dir, seen, stack + (real,))


# ---------------------------------------------------------------- checker


class Checker:
    def __init__(self, pkg: Pkg) -> None:
        self.pkg = pkg
        self.types: dict[str, TypeInfo] = {}
        self.traits: dict[str, TraitInfo] = {}
        self.provides: dict[tuple[str, str], ProvideInfo] = {}
        self.sigs: dict[str, Sig] = {}
        self.agents: list[AgentInfo] = []
        self.routes: list[RouteInfo] = []
        self.egress: dict[str, list[str]] = {}
        self.modules: dict[str, Module] = {}
        self.impl_of: dict[str, Module] = {}
        self.used_names: set[tuple[str, str, str]] = set()  # (file, module, name)
        self.prog = Program()
        self.pending: list[tuple[str, tuple[Ty, ...]]] = []
        self.queued: set[str] = set()
        self.instantiation_paths: dict[str, tuple] = {}
        self.current_instantiation: tuple = ()
        self.outbound: set[str] = set()  # modules that actually call out
        self.serve_extra: dict[str, set[str]] = {}
        self.effects_used: set[str] = set()
        self.public: set[str] = set()

    # -- module tables ---------------------------------------------------

    def all_modules(self) -> list[Module]:
        out = list(self.pkg.stdlib.values())
        out += list(self.pkg.dep_specs.values()) + list(self.pkg.dep_impls.values())
        out += list(self.pkg.spec.values()) + list(self.pkg.impl.values())
        out += list(self.pkg.tests.values())
        return out

    def module_use(self, m: Module) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for u in m.uses:
            if u.path in out:
                raise err(14, m.file, u.line)
            out[u.path] = list(u.names)
        return out

    def mark_used(self, m: Module, mod: str, name: str) -> None:
        self.used_names.add((m.file, mod, name))

    # -- type resolution --------------------------------------------------

    def lookup_type(self, nom: Nom) -> TypeInfo | None:
        return self.types.get(nom.name)

    def resolve_type_name(self, m: Module, name: str, line: int) -> str:
        """A bare Pascal name is local, or comes from a use allowlist (S4)."""
        local = f"{m.path}.{name}"
        if local in self.types or local in self.traits:
            return local
        for u in m.uses:
            if name in u.names:
                cand = f"{u.path}.{name}"
                if cand in self.types or cand in self.traits:
                    self.mark_used(m, u.path, name)
                    return cand
                raise err(96, m.file, line)
        raise err(20, m.file, line)

    def resolve_type(self, m: Module, te: TypeExpr | None, tenv: dict[str, Ty]) -> Ty:
        if te is None:
            raise err(20, m.file, 1)
        parts = [self.resolve_named(m, p, tenv) for p in te.parts]
        if len(parts) == 1:
            return parts[0]
        members: list[Ty] = []
        # S7 restricts union members to nominal types, but the closed stdlib
        # declares `str | ConvertError` and `str | NotFound | DbError`
        # (docs/stdlib.md), and app code must be able to name what it calls.
        # The two locked documents disagree; a scalar member is accepted so the
        # stdlib stays usable. docs/grammar.ebnf has no pattern for a scalar
        # member, so `match` on such a union is still rejected (E070).
        for p, node in zip(parts, te.parts):
            if isinstance(p, Prim) and p.name in T.PRIMS:
                pass
            elif not isinstance(p, (Nom, Var)):
                raise err(25, m.file, node.line)
            if any(p.key == e.key for e in members):
                raise err(25, m.file, node.line)
            members.append(p)
        return Union(tuple(members))

    def resolve_named(self, m: Module, nt: NamedType, tenv: dict[str, Ty]) -> Ty:
        base: Ty
        name = nt.name
        if name in T.PRIMS:
            base = Prim(name)
        elif name == "list":
            if len(nt.args) != 1:
                raise err(29, m.file, nt.line)
            base = ListT(self.resolve_type(m, nt.args[0], tenv))
        elif name == "map":
            if len(nt.args) != 2:
                raise err(29, m.file, nt.line)
            k = self.resolve_type(m, nt.args[0], tenv)
            v = self.resolve_type(m, nt.args[1], tenv)
            if not (isinstance(k, Prim) and k.name in T.MAP_KEYS) and not isinstance(k, Var):
                raise err(29, m.file, nt.line)
            base = MapT(k, v)
        elif name == "Self":
            if "Self" not in tenv:
                raise err(20, m.file, nt.line)
            base = tenv["Self"]
        elif name in T.TYPE_VARS and name in tenv:
            base = tenv[name]
        elif name in T.TYPE_VARS:
            base = Var(name)
        else:
            if "." in name:
                raise err(4, m.file, nt.line)
            fq = self.resolve_type_name(m, name, nt.line)
            if fq not in self.types:
                raise err(20, m.file, nt.line)
            info = self.types[fq]
            args = tuple(self.resolve_type(m, a, tenv) for a in nt.args)
            if len(args) != len(info.tparams):
                raise err(29, m.file, nt.line)
            base = Nom(fq, args)
        if nt.optional:
            return Opt(base)
        return base

    # -- collection -------------------------------------------------------

    def collect(self) -> None:
        self.types["core.secret.Secret"] = TypeInfo(
            kind="opaque", name="core.secret.Secret", module="core.secret"
        )
        groups = [
            (self.pkg.stdlib, True),
            (self.pkg.dep_specs, True),
            (self.pkg.dep_impls, False),
            (self.pkg.spec, True),
            (self.pkg.impl, False),
        ]
        for m in self.pkg.tests.values():
            self.modules.setdefault(m.path, m)
        for group, public in groups:
            for m in group.values():
                self.modules.setdefault(m.path, m)
                for it in m.items:
                    if isinstance(it, TypeDecl):
                        self._decl_type(m, it, public)
                    elif isinstance(it, EnumDecl):
                        self._decl_enum(m, it, public)
        for group, _public in groups:
            for m in group.values():
                for it in m.items:
                    if isinstance(it, TraitDecl):
                        self._decl_trait(m, it)
        for group, public in groups:
            for m in group.values():
                for it in m.items:
                    if isinstance(it, FnDecl):
                        self._decl_fn(m, it, public)
                    elif isinstance(it, AgentDecl):
                        self._decl_agent(m, it)
                    elif isinstance(it, HttpDecl):
                        # S12: one `http` block per module, with at least one route.
                        if not it.routes or any(r.module == m.path for r in self.routes):
                            raise err(91, m.file, it.line)
                        for r in it.routes:
                            self.routes.append(
                                RouteInfo(m.path, r.verb, _unquote(r.path), r.handler, r.line, m.file)
                            )
                    elif isinstance(it, EgressDecl):
                        if m.path in self.egress:
                            raise err(4, m.file, it.line)
                        self.egress[m.path] = [_unquote(s) for s in it.origins]
        for group, _public in groups:
            for m in group.values():
                for it in m.items:
                    if isinstance(it, ProvideDecl):
                        self._decl_provide(m, it)

    def _check_no_builtin_shadow(self, m: Module, name: str, line: int) -> None:
        if name.lower() in T.PRIMS or name in ("Self",):
            raise err(28, m.file, line)

    def _decl_type(self, m: Module, it: TypeDecl, public: bool) -> None:
        fq = f"{m.path}.{it.name}"
        self._check_no_builtin_shadow(m, it.name, it.line)
        if fq in self.types:
            raise err(21, m.file, it.line)
        info = TypeInfo(
            kind="record",
            name=fq,
            module=m.path,
            tparams=tuple(it.tparams),
            line=it.line,
            file=m.file,
            public=public,
        )
        self.types[fq] = info
        if public:
            self.public.add(fq)
        info.decl = it  # type: ignore[attr-defined]

    def _decl_enum(self, m: Module, it: EnumDecl, public: bool) -> None:
        fq = f"{m.path}.{it.name}"
        self._check_no_builtin_shadow(m, it.name, it.line)
        if fq in self.types:
            raise err(21, m.file, it.line)
        info = TypeInfo(
            kind="enum", name=fq, module=m.path, line=it.line, file=m.file, public=public
        )
        self.types[fq] = info
        if public:
            self.public.add(fq)
        info.decl = it  # type: ignore[attr-defined]

    def resolve_bodies_of_types(self) -> None:
        for fq, info in list(self.types.items()):
            decl = getattr(info, "decl", None)
            if decl is None:
                continue
            m = self.modules[info.module]
            tenv = {n: Var(n) for n in info.tparams}
            if isinstance(decl, TypeDecl):
                seen: set[str] = set()
                for f in decl.fields:
                    if f.name in seen:
                        raise err(24, m.file, f.line)
                    seen.add(f.name)
                    info.fields.append((f.name, self.resolve_type(m, f.ty, tenv)))
            elif isinstance(decl, EnumDecl):
                seen = set()
                for v in decl.variants:
                    if v.name in seen:
                        raise err(24, m.file, v.line)
                    seen.add(v.name)
                    if len({f.name for f in v.fields}) != len(v.fields):
                        raise err(24, m.file, v.line)
                    fields = [(f.name, self.resolve_type(m, f.ty, tenv)) for f in v.fields]
                    info.variants.append((v.name, fields))

    def _decl_trait(self, m: Module, it: TraitDecl) -> None:
        fq = f"{m.path}.{it.name}"
        tenv = {n: Var(n) for n in it.tparams}
        tenv["Self"] = Var("Self")
        members: list[Sig] = []
        for sg in it.members:
            members.append(self._sig_of(m, sg, symbol=f"trait:{fq}:{sg.name}", tenv=tenv))
        self.traits[fq] = TraitInfo(
            name=fq, module=m.path, tparams=tuple(it.tparams), members=members, line=it.line, file=m.file
        )

    def _sig_of(self, m: Module, sg: FnSig, symbol: str, tenv: dict[str, Ty] | None = None) -> Sig:
        env = dict(tenv or {})
        for tp in sg.tparams:
            env[tp] = Var(tp)
        params: list[tuple[str, Ty]] = []
        seen: set[str] = set()
        for p in sg.params:
            if p.name in seen:
                raise err(36, m.file, p.line)
            seen.add(p.name)
            params.append((p.name, self.resolve_type(m, p.ty, env)))
        ret = self.resolve_type(m, sg.ret, env)
        effects = frozenset(sg.effects)
        for e in effects:
            if e not in EFFECTS:
                raise err(52, m.file, sg.line)
        return Sig(
            kind=sg.kind,
            module=m.path,
            name=sg.name,
            symbol=symbol,
            tparams=tuple(sg.tparams),
            params=params,
            ret=ret,
            effects=effects,
            contracts=sg.contracts,
            line=sg.line,
            file=m.file,
            mod=m,
        )

    def _decl_fn(self, m: Module, it: FnDecl, public: bool) -> None:
        sg = it.sig
        symbol = f"{m.path}.{sg.name}"
        if sg.kind in ("task", "workflow"):
            if not sg.effects:
                raise err(50, m.file, it.line)
            if "ffi" in sg.effects and not m.path.startswith(STD_PREFIXES):
                raise err(69, m.file, it.line)
        if f"{m.path}.{sg.name}" in self.types:
            raise err(16, m.file, it.line)
        sig = self._sig_of(m, sg, symbol)
        sig.body = it.body
        sig.body_mod = m if it.body is not None else None
        sig.native = m.role == "stdlib"
        existing = self.sigs.get(symbol)
        if existing is not None:
            if m.role in ("impl",):
                _same_sig(existing, sig, m.file, it.line)
                existing.body = it.body
                existing.body_mod = m
                return
            raise err(21, m.file, it.line)
        self.sigs[symbol] = sig
        if public:
            self.public.add(symbol)

    def _decl_agent(self, m: Module, it: AgentDecl) -> None:
        if "ffi" in it.effects:
            raise err(69, m.file, it.line)
        if it.evolve_max is not None:
            if not all(0 < value <= 2147483647 for value in (it.evolve_max, it.evolve_timeout, it.evolve_reservoir)):
                raise err(117, m.file, it.line)
            if "model" not in it.effects:
                raise err(114, m.file, it.line)
        self.agents.append(
            AgentInfo(
                name=it.name,
                module=m.path,
                effects=frozenset(it.effects),
                tasks=tuple(it.tasks),
                evolve_max=it.evolve_max,
                evolve_timeout=it.evolve_timeout,
                evolve_reservoir=it.evolve_reservoir,
                line=it.line,
                file=m.file,
            )
        )

    def _decl_provide(self, m: Module, it: ProvideDecl) -> None:
        trait_fq = self._resolve_trait_ref(m, it.trait, it.line)
        target = self._resolve_provide_target(m, it, trait_fq)
        target_name = target.name if isinstance(target, Nom) else target.key
        key = (trait_fq, target_name)
        info = self.provides.get(key)
        if info is None:
            info = ProvideInfo(trait=trait_fq, target=target, module=m.path, line=it.line, file=m.file)
            self.provides[key] = info
        elif it.members is not None and info.members:
            raise err(81, m.file, it.line)
        if it.members is None:
            return
        trait = self.traits[trait_fq]
        tenv: dict[str, Ty] = {"Self": target}
        # `provide Holder[i64] for Cell` binds the trait's own type parameters.
        if len(it.trait_args) != len(trait.tparams):
            raise err(29, m.file, it.line)
        for name, arg in zip(trait.tparams, it.trait_args):
            tenv[name] = self.resolve_type(m, arg, {})
        names = set()
        for mem in it.members:
            sig = self._sig_of(
                m, mem.sig, symbol=f"provide:{trait_fq}:{target_name}:{mem.sig.name}", tenv=tenv
            )
            sig.self_ty = target
            sig.body = mem.body
            sig.body_mod = m
            decl = trait.member(mem.sig.name)
            if decl is None:
                raise err(82, m.file, mem.line)
            want = [subst(t, tenv) for _n, t in decl.params]
            got = [t for _n, t in sig.params]
            if [w.key for w in want] != [g.key for g in got]:
                raise err(82, m.file, mem.line)
            if subst(decl.ret, tenv).key != sig.ret.key or decl.kind != sig.kind:
                raise err(82, m.file, mem.line)
            if not sig.effects <= decl.effects:
                raise err(83, m.file, mem.line)
            names.add(mem.sig.name)
            info.members[mem.sig.name] = sig
            self.sigs[sig.symbol] = sig
        for decl in trait.members:
            if decl.name not in names:
                raise err(82, m.file, it.line)

    def _resolve_trait_ref(self, m: Module, ref: str, line: int) -> str:
        if "." in ref:
            if ref in self.traits:
                mod, short = ref.rsplit(".", 1)
                self.mark_used(m, mod, short)
                return ref
            raise err(20, m.file, line)
        fq = self.resolve_type_name(m, ref, line)
        if fq not in self.traits:
            raise err(20, m.file, line)
        return fq

    def _resolve_provide_target(self, m: Module, it: ProvideDecl, trait_fq: str) -> Ty:
        if it.target in T.PRIMS:
            if not m.path.startswith(STD_PREFIXES):
                raise err(80, m.file, it.line)
            return Prim(it.target)
        fq = f"{m.path}.{it.target}"
        if fq not in self.types:
            raise err(80, m.file, it.line)
        info = self.types[fq]
        args = tuple(Var(n) for n in info.tparams)
        return Nom(fq, args)


def _contract_shape(value: object) -> object:
    """Compare parsed contract content without source locations or comments."""
    if isinstance(value, Node):
        return (type(value).__name__, tuple(
            (item.name, _contract_shape(getattr(value, item.name)))
            for item in fields(value)
            if item.name not in ("line", "comments", "trailing")
        ))
    if isinstance(value, (list, tuple)):
        return tuple(_contract_shape(item) for item in value)
    return value


def _same_sig(a: Sig, b: Sig, file: str, line: int) -> None:
    if a.kind != b.kind or a.ret.key != b.ret.key:
        raise err(32, file, line)
    if [t.key for _n, t in a.params] != [t.key for _n, t in b.params]:
        raise err(32, file, line)
    if [n for n, _t in a.params] != [n for n, _t in b.params]:
        raise err(32, file, line)
    if a.effects != b.effects:
        raise err(32, file, line)
    if _contract_shape(a.contracts) != _contract_shape(b.contracts):
        raise err(32, file, line)


def _unquote(raw: str) -> str:
    body = raw[1:-1]
    out = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\":
            nxt = body[i + 1]
            out.append({"\\": "\\", '"': '"', "n": "\n", "t": "\t", "r": "\r"}[nxt])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------- declarations


def _http_verbs() -> tuple[str, ...]:
    return ("get", "post", "put", "patch", "delete")


class DeclChecker:
    """Phase 3-4 checks that do not need function bodies."""

    def __init__(self, ck: Checker) -> None:
        self.ck = ck

    def run(self) -> None:
        self.layout()
        self.module_names()
        self.imports()
        self.decl_shapes()
        self.agents()
        self.http()
        self.egress()
        self.visibility()

    def layout(self) -> None:
        pkg = self.ck.pkg
        for path, m in pkg.impl.items():
            if path not in pkg.spec:
                raise err(18, m.file, 1)
        for path, m in pkg.tests.items():
            target = path[len("test.") :]
            if target not in pkg.spec:
                raise err(18, m.file, 1)

    def module_names(self) -> None:
        """lockfile.md graph rules on the names a package may declare.

        No `GOPYT_E*` is assigned to either case, so the closest codes are used
        and the choice is recorded in PROGRESS.md: a stdlib prefix is reported as
        E012 (the module path is not one this package may own) and a collision
        with a dependency name as E044.
        """
        deps = set(self.ck.pkg.manifest.deps) if self.ck.pkg.manifest else set()
        for group in (self.ck.pkg.spec, self.ck.pkg.impl, self.ck.pkg.tests):
            for path, m in group.items():
                head = path.split(".")[0]
                if head == "test":
                    head = path.split(".")[1] if "." in path else head
                if head in STD_PREFIXES:
                    raise err(12, m.file, 1)
                if head in deps:
                    raise err(44, m.file, 1)

    def imports(self) -> None:
        """S21: a `use` names a stdlib module or a module in the package graph."""
        for m in self.ck.all_modules():
            if m.role == "stdlib":
                continue
            owner = self.ck.pkg.module_owner.get(m.path)
            for u in m.uses:
                if u.path not in STDLIB_SOURCE and u.path not in self.ck.modules:
                    raise err(96, m.file, u.line)
                target = self.ck.pkg.module_owner.get(u.path)
                if (
                    u.path not in STDLIB_SOURCE
                    and owner is not None
                    and target is not None
                    and target != owner
                    and target not in self.ck.pkg.package_deps.get(owner, set())
                ):
                    # lockfile.md: a transitive package must also be a direct dep.
                    raise err(43, m.file, u.line)
                for name in u.names:
                    fq = f"{u.path}.{name}"
                    if fq not in self.ck.types and fq not in self.ck.sigs and fq not in self.ck.traits:
                        # S4: the allowlist names something the module declares.
                        raise err(96, m.file, u.line)
                    # A name that appears nowhere outside the `use` lines cannot
                    # be used by anything, so E014 belongs to this phase. The
                    # semantic case still waits for the body pass.
                    if not re.search(rf"\b{re.escape(name)}\b", _body_text(m)):
                        raise err(14, m.file, u.line)

    def obligations(self) -> None:
        """E033: every spec callable and provide has an impl twin (S15).

        Reported after declarations, contracts, and effects, which is the phase
        order docs/diagnostics.md fixes.
        """
        pkg = self.ck.pkg
        for path, m in pkg.spec.items():
            needs = any(
                isinstance(i, FnDecl)
                or (isinstance(i, ProvideDecl) and not self._compiler_filled(m, i))
                for i in m.items
            )
            # The repair is the impl file to write, so name that file, not the spec.
            if needs and path not in pkg.impl:
                raise err(33, _impl_path(m), 1, repair=_impl_stub(m, self.ck))
        for path, spec in pkg.spec.items():
            impl = pkg.impl.get(path)
            for it in spec.items:
                if isinstance(it, FnDecl):
                    if impl is None or _find_fn(impl, it.sig.name) is None:
                        raise err(33, _impl_path(spec), 1, repair=_impl_stub(spec, self.ck))
                if isinstance(it, ProvideDecl) and impl is not None:
                    if self._compiler_filled(spec, it):
                        continue
                    if _find_provide(impl, it) is None:
                        raise err(33, _impl_path(spec), 1, repair=_impl_stub(spec, self.ck))

    def decl_shapes(self) -> None:
        for m in self.ck.all_modules():
            spec_like = m.role in ("spec", "stdlib")
            for it in m.items:
                if isinstance(it, FnDecl):
                    if spec_like and it.body is not None:
                        raise err(31, m.file, it.line)
                    if m.role == "impl" and it.body is None:
                        raise err(33, m.file, it.line)
                elif isinstance(it, ProvideDecl):
                    if spec_like and it.members is not None:
                        raise err(31, m.file, it.line)
                    if m.role == "impl" and it.members is None and not self._compiler_filled(m, it):
                        raise err(33, m.file, it.line)
        for path, impl in self.ck.pkg.impl.items():
            spec = self.ck.pkg.spec[path]
            for it in impl.items:
                if isinstance(it, ProvideDecl) and _find_provide(spec, it) is None:
                    raise err(19, impl.file, it.line)

    def _compiler_filled(self, m: Module, it: ProvideDecl) -> bool:
        """`provide Json for T` has one legal body, so E-D owns it (S24)."""
        try:
            trait = self.ck._resolve_trait_ref(m, it.trait, it.line)
            if trait == "core.convert.Json":
                return True
            if trait == "core.convert.FromStr":
                target = self.ck._resolve_provide_target(m, it, trait)
                info = self.ck.types.get(target.name)
                fields = info.field_types(target.args) if info and info.kind == "record" else []
                return len(fields) == 1 and fields[0][1] in (STR, I64)
            return False
        except CompileError:
            return False

    def agents(self) -> None:
        for ag in self.ck.agents:
            union: set[str] = set()
            seen: set[str] = set()
            if not ag.tasks:
                raise err(94, ag.file, ag.line)
            for name in ag.tasks:
                if name in seen:
                    raise err(94, ag.file, ag.line)
                seen.add(name)
                sig = self.ck.sigs.get(f"{ag.module}.{name}")
                if sig is None or sig.kind not in ("task", "workflow"):
                    raise err(94, ag.file, ag.line)
                union |= set(sig.effects)
            if union != set(ag.effects):
                raise err(95, ag.file, ag.line)

    def http(self) -> None:
        by_module: dict[str, list[RouteInfo]] = {}
        for r in self.ck.routes:
            by_module.setdefault(r.module, []).append(r)
        for module, routes in by_module.items():
            m = self.ck.modules[module]
            seen: set[tuple[str, str]] = set()
            handler_effects: set[str] = set()
            for r in routes:
                if r.verb not in _http_verbs():
                    raise err(90, r.file, r.line)
                _check_route_path(r)
                pat = _route_pattern(r.path)
                # implementer.md 12: two same-method patterns that can match the
                # same path are E091; there is no precedence rule.
                for verb, other in seen:
                    if verb == r.verb and _patterns_overlap(pat, other):
                        raise err(91, r.file, r.line)
                seen.add((r.verb, pat))
                sig = self.ck.sigs.get(f"{module}.{r.handler}")
                # S12: the handler is a `task` in this module.
                if sig is None or sig.kind != "task":
                    raise err(91, r.file, r.line)
                handler_effects |= set(sig.effects)
                self._check_handler_params(r, sig)
            serve = self.ck.sigs.get(f"{module}.serve")
            want_eff = handler_effects | {"network"}
            repair = (
                "task serve() -> unit | ListenError\n"
                "    effects { " + ", ".join(e for e in EFFECTS if e in want_eff) + " }\n"
            )
            if serve is None or serve.kind != "task" or serve.params:
                raise err(93, m.file, routes[0].line, repair=repair)
            if set(serve.effects) != want_eff:
                raise err(93, serve.file, serve.line, repair=repair)
            if serve.body is not None:
                stmts = serve.body.stmts
                call = stmts[0].expr if len(stmts) == 1 and isinstance(stmts[0], ReturnStmt) else None
                if not (isinstance(call, CallExpr) and isinstance(call.callee, NameExpr)
                        and call.callee.parts == ["net", "http", "serve"]
                        and not call.args and not call.targs):
                    raise err(93, serve.file, serve.line)
            self.ck.serve_extra[f"{module}.serve"] = set(handler_effects)

    def _check_handler_params(self, r: RouteInfo, sig: Sig) -> None:
        placeholders = _placeholders(r.path)
        names = [n for n, _t in sig.params]
        body_params = [n for n in names if n not in placeholders]
        if set(placeholders) - set(names):
            raise err(92, r.file, r.line)
        if r.verb in ("get", "delete") and body_params:
            raise err(92, r.file, r.line)
        if r.verb in ("post", "put", "patch") and len(body_params) > 1:
            raise err(92, r.file, r.line)
        for name, ty in sig.params:
            if name in placeholders:
                if ty.key != "str" and not self.ck.has_from_str(ty):
                    raise err(92, r.file, r.line)
                if ty.key != "str":
                    # The server converts the segment through this provide, so
                    # the member must reach the artifact even if no source calls it.
                    target = ty.name if isinstance(ty, Nom) else ty.key
                    self.ck.request_func(
                        f"provide:core.convert.FromStr:{target}:from_str", ()
                    )
            else:
                if not self.ck.is_json(ty):
                    raise err(97, r.file, r.line)
        if sig.ret.key != "unit" and not self.ck.is_json(sig.ret):
            raise err(97, r.file, r.line)

    def egress(self) -> None:
        for module, origins in self.ck.egress.items():
            m = self.ck.modules[module]
            if not origins:
                raise err(4, m.file, 1)
            seen: set[str] = set()
            for o in origins:
                norm = normalize_origin(o)
                if norm is None:
                    raise err(111, m.file, 1)
                if norm in seen:
                    raise err(4, m.file, 1)
                seen.add(norm)

    def unused_egress(self) -> None:
        """security.md: omit `egress` when the module makes no outbound call."""
        for module in sorted(self.ck.egress):
            if module not in self.ck.outbound:
                m = self.ck.modules[module]
                raise err(4, m.file, 1)

    def visibility(self) -> None:
        for symbol in sorted(self.ck.public):
            sig = self.ck.sigs.get(symbol)
            if sig is None:
                continue
            for _n, ty in sig.params:
                self._public_ty(sig, ty)
            self._public_ty(sig, sig.ret)

    def _public_ty(self, sig: Sig, ty: Ty) -> None:
        for nom in _noms_in(ty):
            info = self.ck.types.get(nom.name)
            if info is None:
                raise err(20, sig.file, sig.line)
            if not info.public:
                raise err(35, sig.file, sig.line)


def _mismatch(got: Ty, want: Ty) -> int:
    """E026 when a `T?` was used where `T` is wanted; otherwise E021."""
    if isinstance(got, Opt) and not isinstance(want, Opt) and got.elem.key == want.key:
        return 26
    return 21


def _vars_in(ty: Ty) -> list[Var]:
    if isinstance(ty, Var):
        return [ty]
    if isinstance(ty, Opt):
        return _vars_in(ty.elem)
    if isinstance(ty, ListT):
        return _vars_in(ty.elem)
    if isinstance(ty, MapT):
        return _vars_in(ty.key_ty) + _vars_in(ty.val_ty)
    if isinstance(ty, Nom):
        out: list[Var] = []
        for a in ty.args:
            out += _vars_in(a)
        return out
    if isinstance(ty, Union):
        out = []
        for mem in ty.members:
            out += _vars_in(mem)
        return out
    return []


def _noms_in(ty: Ty) -> list[Nom]:
    if isinstance(ty, Nom):
        out = [ty]
        for a in ty.args:
            out += _noms_in(a)
        return out
    if isinstance(ty, Opt):
        return _noms_in(ty.elem)
    if isinstance(ty, ListT):
        return _noms_in(ty.elem)
    if isinstance(ty, MapT):
        return _noms_in(ty.key_ty) + _noms_in(ty.val_ty)
    if isinstance(ty, Union):
        out = []
        for mem in ty.members:
            out += _noms_in(mem)
        return out
    return []


def _find_fn(m: Module, name: str) -> FnDecl | None:
    for it in m.items:
        if isinstance(it, FnDecl) and it.sig.name == name:
            return it
    return None


def _find_provide(m: Module, want: ProvideDecl) -> ProvideDecl | None:
    for it in m.items:
        if isinstance(it, ProvideDecl) and it.trait == want.trait and it.target == want.target:
            return it
    return None


def _impl_path(spec: Module) -> str:
    """The impl twin of a spec file (S3: impl/ mirrors spec/ one to one)."""
    if spec.file.startswith("spec/"):
        return "impl/" + spec.file[len("spec/"):]
    parent, relative = spec.file.rsplit("/spec/", 1)
    return parent + "/impl/" + relative


def _impl_stub(spec: Module, checker=None) -> str:
    from gopyt.elaborate import impl_stub
    return impl_stub(spec, checker)


def _placeholders(path: str) -> list[str]:
    out = []
    for seg in path.split("/"):
        if seg.startswith("{") and seg.endswith("}"):
            out.append(seg[1:-1])
    return out


def _body_text(m: Module) -> str:
    """The module source with its `use` lines removed."""
    return "\n".join(
        line for line in m.source.split("\n") if not line.startswith("use ")
    )


def _patterns_overlap(left: str, right: str) -> bool:
    """True when one concrete path could match both patterns."""
    a = left.split("/")
    b = right.split("/")
    if len(a) != len(b):
        return False
    return all(x == y or x == "{}" or y == "{}" for x, y in zip(a, b))


def _route_pattern(path: str) -> str:
    segs = []
    for seg in path.split("/"):
        segs.append("{}" if seg.startswith("{") and seg.endswith("}") else seg)
    return "/".join(segs)


def _check_route_path(r: RouteInfo) -> None:
    p = r.path
    if not p.startswith("/") or not p.isascii():
        raise err(91, r.file, r.line)
    if p != "/" and p.endswith("/"):
        raise err(91, r.file, r.line)
    if "?" in p or "#" in p or "%" in p:
        raise err(91, r.file, r.line)
    segs = p.split("/")[1:]
    if p == "/":
        return
    names: set[str] = set()
    for seg in segs:
        if seg in ("", ".", ".."):
            raise err(91, r.file, r.line)
        if seg.startswith("{") or seg.endswith("}"):
            if not (seg.startswith("{") and seg.endswith("}")):
                raise err(91, r.file, r.line)
            name = seg[1:-1]
            if not name or name in names:
                raise err(91, r.file, r.line)
            names.add(name)


def normalize_origin(text: str) -> str | None:
    """docs/security.md: scheme://host[:port], nothing else."""
    if "://" not in text:
        return None
    scheme, rest = text.split("://", 1)
    if scheme not in ("http", "https"):
        return None
    if rest == "" or "/" in rest or "@" in rest or "?" in rest or "#" in rest:
        return None
    if not rest.isascii():
        return None
    host = rest
    port = 443 if scheme == "https" else 80
    if ":" in rest:
        host, port_s = rest.rsplit(":", 1)
        significant = port_s.lstrip("0") or "0"
        if len(significant) > 5 or not port_s.isdigit() or not (1 <= int(significant) <= 65535):
            return None
        port = int(significant)
    host = host.lower()
    if not host or "*" in host:
        return None
    for ch in host:
        if not (ch.isalnum() or ch in ".-"):
            return None
    if scheme == "http" and host not in ("127.0.0.1", "localhost"):
        return None
    return f"{scheme}://{host}:{port}"


# ---------------------------------------------------------------- bodies


@dataclass
class CaptureRes:
    index: int


@dataclass
class FieldPathRes:
    """`order.id` parses as one dotted name; this is its load path."""

    base: object
    indices: list[int]


@dataclass
class LocalRes:
    slot: int


ARITH = ("+", "-", "*", "/", "%")
ORDER = ("<", ">", "<=", ">=")


class FnChecker:
    """Checks one concrete instantiation and records what the emitter needs."""

    def __init__(
        self,
        ck: Checker,
        sig: Sig,
        targs: tuple[Ty, ...],
        capture: dict[str, tuple[int, Ty]] | None = None,
        symbol: str | None = None,
        parent: str = "",
        generic: bool = False,
    ) -> None:
        self.ck = ck
        self.sig = sig
        self.m = sig.body_mod or sig.mod or ck.modules[sig.module]
        self.tenv = sig.env(targs)
        self.capture = capture or {}
        # A generic declaration that is never instantiated still gets one body
        # pass, with its own type variables left standing. Nothing is emitted.
        self.generic = generic
        self.own_vars = set(sig.tparams) if generic else set()
        self.used: set[str] = set()
        self.pure = sig.kind == "fn"
        self.infer = sig.kind == "test"
        params = [(n, subst(t, self.tenv)) for n, t in sig.params]
        self.fc = FuncCheck(
            symbol=symbol or sig.symbol,
            targs=targs,
            kind=sig.kind,
            module=sig.module,
            file=sig.file,
            params=params,
            ret=subst(sig.ret, self.tenv),
            effects=frozenset(sig.effects),
            contracts=list(sig.contracts),
            body=sig.body,
            parent=parent,
        )
        self.fc.file = self.m.file
        self.scopes: list[dict[str, tuple[int, Ty]]] = [{}]
        self.literal_urls: dict[str, str] = {}
        for name, ty in params:
            self.scopes[0][name] = (self.new_slot(ty), ty)

    # -- infrastructure --------------------------------------------------

    def new_slot(self, ty: Ty) -> int:
        self.fc.local_types.append(ty)
        return len(self.fc.local_types) - 1

    def bind(self, name: str, ty: Ty, line: int) -> int:
        for scope in self.scopes:
            if name in scope:
                raise err(36, self.m.file, line)
        slot = self.new_slot(ty)
        self.scopes[-1][name] = (slot, ty)
        return slot

    def lookup(self, name: str) -> tuple[int, Ty] | None:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    def note(self, e: object, ty: Ty) -> Ty:
        self.fc.ty[id(e)] = ty
        return ty

    def use_effect(self, effects: frozenset[str], line: int) -> None:
        if self.pure and effects:
            raise err(53, self.m.file, line)
        if self.infer:
            self.used |= set(effects) - {"ffi"}
            return
        # S9: `ffi` on a stdlib native does not propagate; callers list only
        # database.read / database.write. App code can never declare it (E069).
        missing = set(effects) - set(self.fc.effects) - {"ffi"}
        if missing:
            raise err(50, self.m.file, line)
        self.used |= set(effects)

    # -- entry -----------------------------------------------------------

    def run(self) -> FuncCheck:
        if any(c.kind == "ensures" and c.open_id is None for c in self.fc.contracts):
            self.fc.result_slot = self.new_slot(self.fc.ret)
        for c in self.fc.contracts:
            if c.open_id is not None:
                continue
            saved = self.ck.used_names
            self.ck.used_names = set()
            try:
                self.check_contract(c)
            finally:
                fresh = self.ck.used_names
                self.ck.used_names = saved | fresh
            # A contract is declared verbatim in both spec/ and impl/ (E032), so
            # a name it uses is used by both files (S4 unused-use), whether or
            # not an earlier body in this file already used that name.
            decl = getattr(self.sig, "mod", None)
            if decl is not None and decl.file != self.m.file:
                for file, mod, name in fresh:
                    if file == self.m.file:
                        self.ck.used_names.add((decl.file, mod, name))
        if self.fc.body is None:
            raise err(33, self.m.file, self.sig.line)
        term = self.block(self.fc.body)
        if not term:
            raise err(73, self.m.file, self.fc.body.line)
        if self.infer:
            self.fc.effects = frozenset(self.used)
        else:
            unused = set(self.fc.effects) - self.used - self.ck.extra_effects(self.fc.symbol)
            if unused:
                raise err(51, self.m.file, self.sig.line)
        return self.fc

    def contracts_only(self) -> None:
        """Check a declaration's contracts without a body.

        docs/diagnostics.md puts contracts before spec/impl obligations, so a
        `requires` that is not `bool` must be reported even when the impl twin
        is still missing.
        """
        if any(c.kind == "ensures" and c.open_id is None for c in self.fc.contracts):
            self.fc.result_slot = self.new_slot(self.fc.ret)
        for c in self.fc.contracts:
            if c.open_id is None:
                self.check_contract(c)

    def check_contract(self, c: Contract) -> None:
        scope: dict[str, tuple[int, Ty]] = {}
        for name, ty in self.fc.params:
            slot, _t = self.scopes[0][name]
            scope[name] = (slot, ty)
        if c.kind == "ensures":
            scope["result"] = (self.fc.result_slot, self.fc.ret)
        self.scopes.append(scope)
        saved_pure = self.pure
        self.pure = True  # S11: contracts may call fn only
        try:
            ty = self.expr(c.expr, BOOL)
            if ty.key != "bool":
                raise err(60, self.m.file, c.line)
            self._contract_no_f64(c)
        except CompileError as exc:
            if exc.diag.code == 53:
                raise err(62, self.m.file, c.line)
            raise
        finally:
            self.pure = saved_pure
            self.scopes.pop()

    def _contract_no_f64(self, c: Contract) -> None:
        for ty in self.fc.ty.values():
            if ty.key == "f64":
                raise err(61, self.m.file, c.line)

    # -- statements ------------------------------------------------------

    def block(self, b: Block) -> bool:
        self.scopes.append({})
        term = False
        for st in b.stmts:
            if term:
                raise err(73, self.m.file, st.line)
            term = self.stmt(st)
        self.scopes.pop()
        return term

    def stmt(self, st: object) -> bool:
        if isinstance(st, UnresolvedStmt):
            if self.m.role == "spec":
                raise err(34, self.m.file, st.line)
            raise err(30, self.m.file, st.line)
        if isinstance(st, BindStmt):
            ty = self.expr(st.expr)
            if isinstance(ty, NoneTy):
                raise err(21, self.m.file, st.line)
            self.fc.slot[id(st)] = self.bind(st.name, ty, st.line)
            url = _literal_url(st.expr)
            if url is not None:
                self.literal_urls[st.name] = url  # security.md: literal URLs fail check
            return False
        if isinstance(st, ReturnStmt):
            ty = self.expr(st.expr, self.fc.ret)
            if not assignable(ty, self.fc.ret):
                raise err(_mismatch(ty, self.fc.ret), self.m.file, st.line)
            return True
        if isinstance(st, IfStmt):
            ct = self.expr(st.cond, BOOL)
            if ct.key != "bool":
                raise err(21, self.m.file, st.line)
            a = self.block(st.then)
            b = self.block(st.els) if st.els else False
            return a and b
        if isinstance(st, ForStmt):
            it = self.expr(st.iter)
            if not isinstance(it, ListT):
                raise err(72, self.m.file, st.line)
            self.scopes.append({})
            self.fc.for_list[id(st)] = self.new_slot(it)
            self.fc.for_idx[id(st)] = self.new_slot(I64)
            self.fc.slot[id(st)] = self.bind(st.name, it.elem, st.line)
            self.block(st.body)
            self.scopes.pop()
            return False
        if isinstance(st, ExprStmt):
            self.expr(st.expr)
            return False
        raise err(11, self.m.file, getattr(st, "line", 1))

    # -- expressions -----------------------------------------------------

    def expr(self, e: object, expect: Ty | None = None) -> Ty:
        line = getattr(e, "line", 1)
        if isinstance(e, IntLit):
            return self.note(e, I64)
        if isinstance(e, FloatLit):
            if not math.isfinite(float(e.value)):
                raise err(118, self.m.file, line)
            return self.note(e, F64)
        if isinstance(e, StrLit):
            return self.note(e, STR)
        if isinstance(e, BoolLit):
            return self.note(e, BOOL)
        if isinstance(e, UnitLit):
            return self.note(e, UNIT)
        if isinstance(e, NoneLit):
            if isinstance(expect, Opt):
                return self.note(e, expect)
            return self.note(e, NONE)
        if isinstance(e, SomeExpr):
            inner_expect = expect.elem if isinstance(expect, Opt) else None
            inner = self.expr(e.inner, inner_expect)
            if isinstance(inner, NoneTy):
                raise err(21, self.m.file, line)
            return self.note(e, Opt(inner))
        if isinstance(e, NameExpr):
            return self.name_expr(e, expect)
        if isinstance(e, FieldExpr):
            base = self.expr(e.base)
            if not isinstance(base, Nom):
                raise err(21, self.m.file, line)
            info = self.ck.types.get(base.name)
            if info is None or info.kind != "record":
                raise err(23, self.m.file, line)
            for i, (fname, fty) in enumerate(info.field_types(base.args)):
                if fname == e.name:
                    self.fc.res[id(e)] = FieldRes(i)
                    return self.note(e, fty)
            raise err(23, self.m.file, line)
        if isinstance(e, CallExpr):
            return self.call(e, expect)
        if isinstance(e, ConstructExpr):
            return self.construct(e)
        if isinstance(e, BinExpr):
            return self.binary(e)
        if isinstance(e, UnaryExpr):
            inner = self.expr(e.inner)
            if e.op == "not":
                if inner.key != "bool":
                    raise err(21, self.m.file, line)
                return self.note(e, BOOL)
            if inner.key != "i64":
                raise err(21, self.m.file, line)
            return self.note(e, I64)
        if isinstance(e, MatchExpr):
            return self.match(e, expect)
        if isinstance(e, ParallelExpr):
            return self.parallel(e, expect)
        raise err(11, self.m.file, line)

    def name_expr(self, e: NameExpr, expect: Ty | None) -> Ty:
        line = e.line
        base = self.local_base(e.parts[0])
        if base is not None:
            res, ty = base
            if len(e.parts) == 1:
                self.fc.res[id(e)] = res
                return self.note(e, ty)
            indices: list[int] = []
            for name in e.parts[1:]:
                if not isinstance(ty, Nom):
                    raise err(23, self.m.file, line)
                info = self.ck.types.get(ty.name)
                if info is None or info.kind != "record":
                    raise err(23, self.m.file, line)
                hit = None
                for i, (fname, fty) in enumerate(info.field_types(ty.args)):
                    if fname == name:
                        hit = (i, fty)
                        break
                if hit is None:
                    raise err(23, self.m.file, line)
                indices.append(hit[0])
                ty = hit[1]
            self.fc.res[id(e)] = FieldPathRes(res, indices)
            return self.note(e, ty)
        if len(e.parts) == 1:
            raise err(74, self.m.file, line)
        if len(e.parts) >= 2 and e.parts[-1][:1].isupper() and e.parts[-2][:1].isupper():
            return self.note(e, self.enum_value(e, e.parts, [], line))
        raise err(74, self.m.file, line)

    def local_base(self, name: str) -> tuple[object, Ty] | None:
        found = self.lookup(name)
        if found is not None:
            return LocalRes(found[0]), found[1]
        if name in self.capture:
            idx, ty = self.capture[name]
            return CaptureRes(idx), ty
        return None

    def enum_value(self, node: object, parts: list[str], fields: list, line: int) -> Ty:
        type_parts = parts[:-1]
        variant = parts[-1]
        if len(type_parts) > 1:
            raise err(4, self.m.file, line)
        fq = self.ck.resolve_type_name(self.m, type_parts[0], line)
        info = self.ck.types.get(fq)
        if info is None or info.kind != "enum":
            raise err(20, self.m.file, line)
        idx = info.variant_index(variant)
        if idx < 0:
            raise err(20, self.m.file, line)
        nom = Nom(fq)
        decl = info.variant_fields(idx)
        order = self.check_fields(decl, fields, line)
        self.fc.res[id(node)] = ConstructRes(nom, "enum", idx, order)
        if not self.generic:
            self.ck.request_type(nom)
        return nom

    def check_fields(self, decl: list[tuple[str, Ty]], given: list, line: int) -> list[int]:
        names = [n for n, _t in decl]
        seen: dict[str, int] = {}
        for i, (fname, fexpr) in enumerate(given):
            if fname not in names:
                raise err(24, self.m.file, line)
            if fname in seen:
                raise err(24, self.m.file, line)
            seen[fname] = i
        for n in names:
            if n not in seen:
                raise err(23, self.m.file, line)
        order = []
        for n, want in decl:
            i = seen[n]
            got = self.expr(given[i][1], want)
            if not assignable(got, want):
                raise err(_mismatch(got, want), self.m.file, line)
            order.append(i)
        return order

    def construct(self, e: ConstructExpr) -> Ty:
        parts = e.ty.split(".")
        if e.enum:
            return self.note(e, self.enum_value(e, parts, e.fields, e.line))
        if len(parts) > 1:
            raise err(4, self.m.file, e.line)
        fq = self.ck.resolve_type_name(self.m, parts[0], e.line)
        info = self.ck.types.get(fq)
        if info is None:
            raise err(20, self.m.file, e.line)
        if info.kind == "opaque":
            raise err(21, self.m.file, e.line)
        if info.kind != "record":
            raise err(20, self.m.file, e.line)
        if info.tparams:
            env: dict[str, Ty] = {}
            for fname, fty in info.fields:
                for gname, gexpr in e.fields:
                    if gname == fname:
                        got = self.expr(gexpr)
                        unify(fty, got, env)
            args = tuple(env.get(tp, Var(tp)) for tp in info.tparams)
            if any(has_var(a) for a in args) and not self.generic:
                raise err(29, self.m.file, e.line)
        else:
            args = ()
        nom = Nom(fq, args)
        order = self.check_fields(info.field_types(args), e.fields, e.line)
        self.fc.res[id(e)] = ConstructRes(nom, "record", -1, order)
        if not self.generic:
            self.ck.request_type(nom)
        return self.note(e, nom)

    def binary(self, e: BinExpr) -> Ty:
        line = e.line
        if e.op in ("and", "or"):
            lt = self.expr(e.left, BOOL)
            rt = self.expr(e.right, BOOL)
            if lt.key != "bool" or rt.key != "bool":
                raise err(21, self.m.file, line)
            return self.note(e, BOOL)
        lt = self.expr(e.left)
        rt = self.expr(e.right, lt if not isinstance(lt, NoneTy) else None)
        if e.op in ("==", "!="):
            if lt.key == "f64" or rt.key == "f64":
                raise err(99, self.m.file, line)
            if isinstance(lt, NoneTy) or isinstance(rt, NoneTy):
                other = rt if isinstance(lt, NoneTy) else lt
                if not isinstance(other, Opt):
                    raise err(21, self.m.file, line)
                return self.note(e, BOOL)
            if lt.key != rt.key:
                raise err(21, self.m.file, line)
            if not supports_eq(lt, self.ck.lookup_type):
                raise err(99, self.m.file, line)
            return self.note(e, BOOL)
        if lt.key != "i64" or rt.key != "i64":
            raise err(21, self.m.file, line)
        if e.op in ORDER:
            return self.note(e, BOOL)
        if e.op in ARITH:
            return self.note(e, I64)
        raise err(21, self.m.file, line)

    # -- match -----------------------------------------------------------

    def match(self, e: MatchExpr, expect: Ty | None) -> Ty:
        scrut = self.expr(e.scrut)
        line = e.line
        res = MatchRes(mode="", scrut=scrut)
        if isinstance(scrut, Opt):
            res.mode = "optional"
            self._match_optional(e, scrut, res)
        elif isinstance(scrut, Union):
            res.mode = "union"
            self._match_union(e, scrut, res)
        elif isinstance(scrut, Nom) and self.ck.kind_of(scrut) == "enum":
            res.mode = "enum"
            self._match_enum(e, scrut, res)
        else:
            raise err(70, self.m.file, line)
        self.fc.res[id(e)] = res
        out: Ty | None = expect
        # S7: inside an arm the scrutinee's runtime nominal type is that member,
        # so a named scrutinee narrows. Without this an opaque member (Secret)
        # or a marker record could never be used.
        narrow = None
        if res.mode == "union" and isinstance(e.scrut, NameExpr) and len(e.scrut.parts) == 1:
            hit = self.lookup(e.scrut.parts[0])
            if hit is not None:
                narrow = (e.scrut.parts[0], hit[0])
        for arm, armres in zip(e.arms, res.arms):
            self.scopes.append({})
            if narrow is not None and armres.member is not None:
                self.scopes[-1][narrow[0]] = (narrow[1], armres.member)
            for (slot, _idx), name in zip(armres.binds, _bind_names(arm.pattern)):
                self.scopes[-1][name] = (slot, self.fc.local_types[slot])
            ty = self.expr(arm.body, out)
            self.scopes.pop()
            if out is None:
                out = ty
            elif not assignable(ty, out):
                raise err(21, self.m.file, arm.line)
        if out is None:
            raise err(70, self.m.file, line)
        return self.note(e, out)

    def _match_optional(self, e: MatchExpr, scrut: Opt, res: MatchRes) -> None:
        seen: set[str] = set()
        for arm in e.arms:
            p = arm.pattern
            if isinstance(p, PatNone):
                tag, name = 0, None
            elif isinstance(p, PatSome):
                tag, name = 1, p.name
            else:
                raise err(70, self.m.file, arm.line)
            key = "none" if tag == 0 else "some"
            if key in seen:
                raise err(70, self.m.file, arm.line)
            seen.add(key)
            binds = []
            if name is not None:
                binds.append((self.new_slot(scrut.elem), 0))
            res.arms.append(ArmRes("optional", tag, None, binds))
        if seen != {"some", "none"}:
            raise err(70, self.m.file, e.line)

    def _match_enum(self, e: MatchExpr, scrut: Nom, res: MatchRes) -> None:
        info = self.ck.types[scrut.name]
        seen: set[int] = set()
        for arm in e.arms:
            p = arm.pattern
            if not isinstance(p, PatType):
                raise err(70, self.m.file, arm.line)
            parts = p.name.split(".")
            if len(parts) != 2:
                raise err(70, self.m.file, arm.line)
            if self.ck.resolve_type_name(self.m, parts[0], arm.line) != scrut.name:
                raise err(70, self.m.file, arm.line)
            idx = info.variant_index(parts[1])
            if idx < 0 or idx in seen:
                raise err(70, self.m.file, arm.line)
            seen.add(idx)
            fields = info.variant_fields(idx, scrut.args)
            res.arms.append(ArmRes("enum", idx, scrut, self._pattern_binds(p, fields, arm.line)))
        if len(seen) != len(info.variants):
            raise err(70, self.m.file, e.line)

    def _match_union(self, e: MatchExpr, scrut: Union, res: MatchRes) -> None:
        wanted = {m.key: m for m in scrut.members}
        seen: set[str] = set()
        for arm in e.arms:
            p = arm.pattern
            if not isinstance(p, PatType):
                raise err(70, self.m.file, arm.line)
            parts = p.name.split(".")
            if len(parts) == 2:
                # Enum member written Type.Variant is not a union member pattern.
                raise err(70, self.m.file, arm.line)
            if parts[0] in T.PRIMS:
                # grammar.ebnf Pattern: ScalarBuiltin names a scalar member.
                if p.binds or parts[0] not in wanted or parts[0] in seen:
                    raise err(70, self.m.file, arm.line)
                seen.add(parts[0])
                res.arms.append(ArmRes("union", 0, None, [], scalar=parts[0]))
                continue
            fq = self.ck.resolve_type_name(self.m, parts[0], arm.line)
            nom = None
            for key, mem in wanted.items():
                if isinstance(mem, Nom) and mem.name == fq:
                    nom = mem
                    break
            if nom is None or nom.key in seen:
                raise err(70, self.m.file, arm.line)
            seen.add(nom.key)
            info = self.ck.types[nom.name]
            fields = info.field_types(nom.args) if info.kind == "record" else []
            res.arms.append(ArmRes("union", 0, nom, self._pattern_binds(p, fields, arm.line)))
        if seen != set(wanted):
            raise err(70, self.m.file, e.line)

    def _pattern_binds(
        self, p: PatType, fields: list[tuple[str, Ty]], line: int
    ) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        names = [n for n, _t in fields]
        for b in p.binds:
            if b not in names:
                raise err(24, self.m.file, line)
            idx = names.index(b)
            out.append((self.new_slot(fields[idx][1]), idx))
        return out

    # -- parallel --------------------------------------------------------

    def parallel(self, e: ParallelExpr, expect: Ty | None) -> Ty:
        line = e.line
        if e.max_n <= 0 or e.timeout_ms <= 0 or e.max_n > 65535 or e.timeout_ms > 4294967295:
            raise err(54, self.m.file, line)
        if not e.arms or len(e.arms) > 65535:
            raise err(54, self.m.file, line)
        self.use_effect(frozenset({"time"}), line)
        elem_expect = expect.elem if isinstance(expect, ListT) else None
        arms: list[ParArmRes] = []
        elem: Ty | None = elem_expect
        for i, arm in enumerate(e.arms):
            # Outer locals first (ascending slot), then anything this frame
            # itself received through its own capture record: a nested
            # `parallel` may reference a name its parent arm captured.
            free = self._free_locals(arm)
            free.sort(key=lambda item: (0, item[1][1]) if item[1][0] == "local" else (1, item[1][1]))
            cap_names = [name for name, _v in free]
            cap_types = [v[2] for _n, v in free]
            slots = [
                LocalRes(v[1]) if v[0] == "local" else CaptureRes(v[1]) for _n, v in free
            ]
            cap_type = self.ck.capture_type(self.fc, i, cap_names, cap_types)
            child_sig = Sig(
                kind="fn",
                module=self.fc.module,
                name=f"arm{i}",
                symbol=f"arm:{self.fc.key}:{i}",
                params=[("capture", cap_type)],
                ret=elem or UNIT,
                effects=frozenset(self.fc.effects),
                body=None,
                line=line,
                file=self.fc.file,
                mod=self.m,
                body_mod=self.m,
            )
            child = FnChecker(
                self.ck,
                child_sig,
                (),
                capture={n: (k, t) for k, (n, t) in enumerate(zip(cap_names, cap_types))},
                symbol=child_sig.symbol,
                parent=self.fc.key,
            )
            child.scopes = [{}]
            child.fc.local_types = [cap_type]
            child.fc.params = [("capture", cap_type)]
            child.pure = self.pure
            child.infer = True
            ty = child.expr(arm, elem)
            if elem is None:
                elem = ty
            elif not assignable(ty, elem):
                raise err(55, self.m.file, line)
            child.fc.ret = elem
            child.fc.body = arm
            child.fc.kind = "arm"
            child.fc.effects = frozenset(child.used)
            self.use_effect(child.fc.effects, line)
            self.ck.add_func(child.fc)
            arms.append(ParArmRes(child_sig.symbol, slots, cap_names, cap_types))
        if elem is None:
            raise err(55, self.m.file, line)
        self.fc.res[id(e)] = ParallelRes(arms, elem)
        return self.note(e, ListT(elem))

    def _free_locals(self, node: object) -> list[tuple[str, tuple[str, int, Ty]]]:
        """Names an arm reads from its enclosing frame, with where they live."""
        found: dict[str, tuple[str, int, Ty]] = {}
        bound: set[str] = set()

        def walk(n: object) -> None:
            if isinstance(n, NameExpr) and n.parts:
                name = n.parts[0]
                if name not in bound:
                    hit = self.lookup(name)
                    if hit is not None:
                        found[name] = ("local", hit[0], hit[1])
                    elif name in self.capture:
                        index, ty = self.capture[name]
                        found[name] = ("capture", index, ty)
                return
            if isinstance(n, MatchExpr):
                walk(n.scrut)
                for arm in n.arms:
                    added = set(_bind_names(arm.pattern)) - bound
                    bound.update(added)
                    walk(arm.body)
                    bound.difference_update(added)
                return
            for value in getattr(n, "__dict__", {}).values():
                if isinstance(value, list):
                    for v in value:
                        if isinstance(v, tuple):
                            for w in v:
                                walk(w)
                        else:
                            walk(v)
                elif hasattr(value, "__dict__"):
                    walk(value)

        walk(node)
        return list(found.items())


def _bind_names(pattern: object) -> list[str]:
    if isinstance(pattern, PatSome):
        return [pattern.name]
    if isinstance(pattern, PatType):
        return list(pattern.binds)
    return []


def _call_parts(e: CallExpr) -> list[str]:
    if isinstance(e.callee, NameExpr):
        return e.callee.parts
    if isinstance(e.callee, FieldExpr):
        raise err(37, "", getattr(e, "line", 1))
    raise err(74, "", getattr(e, "line", 1))


def _fn_call(self: FnChecker, e: CallExpr, expect: Ty | None) -> Ty:
    line = e.line
    if isinstance(e.callee, FieldExpr):
        raise err(37, self.m.file, line)
    if not isinstance(e.callee, NameExpr):
        raise err(74, self.m.file, line)
    parts = e.callee.parts
    if parts[-1][:1].isupper():
        raise err(74, self.m.file, line)
    if len(parts) > 1 and self.local_base(parts[0]) is not None:
        raise err(37, self.m.file, line)  # value.fn(...) is not a call form (S13)
    targs = tuple(self.ck.resolve_type(self.m, t, self.tenv) for t in e.targs)
    if len(parts) >= 2 and parts[-2][:1].isupper():
        return self.note(e, self.trait_call(e, parts, targs, expect))
    if len(parts) == 1:
        symbol = f"{self.m.path}.{parts[0]}"
        sig = self.ck.sigs.get(symbol)
        if sig is None or sig.kind == "test":
            # S17: a `test` is a declaration, not a callable.
            raise err(74, self.m.file, line)
    else:
        module = ".".join(parts[:-1])
        symbol = ".".join(parts)
        if module == self.m.path:
            # S4: a same-module call is the bare snake name.
            raise err(4, self.m.file, line, repair=f"{parts[-1]}(...)\n")
        sig = self.ck.sigs.get(symbol)
        if sig is None:
            if module in self.ck.modules or module in STDLIB_SOURCE:
                raise err(96, self.m.file, line)
            raise err(74, self.m.file, line)
        self.require_use(module, parts[-1], line)
    ret = self.apply(sig, targs, e.args, expect, line)
    if symbol == self.fc.symbol and sig.tparams:
        current = tuple(self.tenv.get(tp, Var(tp)).key for tp in sig.tparams)
        if current != tuple(ty.key for ty in self.last_targs):
            raise err(29, self.m.file, line)
    self.ck.guard_stdlib_call(self, symbol, e, self.last_targs, line)
    if not self.generic:
        self.ck.request_func(symbol, self.last_targs)
    self.fc.res[id(e)] = CallRes(symbol, self.last_targs, sig.kind, sig.effects, ret)
    return self.note(e, ret)


def _require_use(self: FnChecker, module: str, name: str, line: int) -> None:
    for u in self.m.uses:
        if u.path == module:
            if name not in u.names:
                names = sorted(set(u.names) | {name}, key=_use_sort)
                repair = f"use {module} {{ {', '.join(names)} }}\n"
                raise err(13, self.m.file, line, repair=repair)
            self.ck.mark_used(self.m, module, name)
            return
    raise err(13, self.m.file, line, repair=f"use {module} {{ {name} }}\n")


def _use_sort(name: str) -> tuple[int, bytes]:
    return (0 if name[:1].isupper() else 1, name.encode("utf-8"))


def _apply(
    self: FnChecker,
    sig: Sig,
    targs: tuple[Ty, ...],
    args: list,
    expect: Ty | None,
    line: int,
) -> Ty:
    if len(args) != len(sig.params):
        raise err(22, self.m.file, line)
    env: dict[str, Ty] = {}
    if sig.self_ty is not None:
        env["Self"] = sig.self_ty
    if targs:
        if len(targs) != len(sig.tparams):
            raise err(29, self.m.file, line)
        env.update({n: t for n, t in zip(sig.tparams, targs)})
    for (pname, pty), arg in zip(sig.params, args):
        want = subst(pty, env)
        if has_var(want):
            got = self.expr(arg)
            if sig.symbol in SECRET_SINKS and _has_secret(self.ck, got):
                raise err(112, self.m.file, line)
            if not unify(want, got, env):
                raise err(21, self.m.file, line)
        else:
            got = self.expr(arg, want)
            # docs/security.md: these three never accept an opaque Secret.
            if sig.symbol in SECRET_SINKS and _has_secret(self.ck, got):
                raise err(112, self.m.file, line)
            if not assignable(got, want):
                raise err(_mismatch(got, want), self.m.file, line)
    if sig.symbol == "core.test.assert_eq" and "T" in env:
        # stdlib.md: assert_eq applies the S20 equality rule recursively, so a
        # type without structural equality (f64, Secret) is E099 at check time.
        if not supports_eq(env["T"], self.ck.lookup_type):
            raise err(99, self.m.file, line)
    ret = subst(sig.ret, env)
    if has_var(ret) and expect is not None:
        unify(ret, expect, env)
        ret = subst(sig.ret, env)
    missing = [tp for tp in sig.tparams if tp not in env]
    if self.generic:
        # Leftovers must be this function's own variables, not free unknowns.
        leftover = {v.name for v in _vars_in(ret)} | {
            v.name for tp in missing for v in _vars_in(Var(tp))
        }
        if leftover - self.own_vars:
            raise err(29, self.m.file, line)
        self.use_effect(sig.effects, line)
        self.last_targs = tuple(env.get(tp, Var(tp)) for tp in sig.tparams)
        return ret
    if missing or has_var(ret):
        raise err(29, self.m.file, line)
    self.use_effect(sig.effects, line)
    self.last_targs = tuple(env[tp] for tp in sig.tparams)
    for nom in _noms_in(ret):
        self.ck.request_type(nom)
    return ret


def _trait_call(
    self: FnChecker, e: CallExpr, parts: list[str], targs: tuple[Ty, ...], expect: Ty | None
) -> Ty:
    line = e.line
    trait_ref = ".".join(parts[:-1])
    member = parts[-1]
    trait_fq = self.ck._resolve_trait_ref(self.m, trait_ref, line)
    trait = self.ck.traits[trait_fq]
    decl = trait.member(member)
    if decl is None:
        raise err(74, self.m.file, line)
    if len(e.args) != len(decl.params):
        raise err(22, self.m.file, line)
    self_ty: Ty | None = targs[0] if targs else None
    if self_ty is None:
        for (pname, pty), arg in zip(decl.params, e.args):
            if pty.key == "Self":
                self_ty = self.expr(arg)
                break
    if self_ty is None and expect is not None:
        env: dict[str, Ty] = {}
        if unify(decl.ret, expect, env) and "Self" in env:
            self_ty = env["Self"]
        elif isinstance(expect, Union) or isinstance(decl.ret, Union):
            for mem in members_of(decl.ret):
                if mem.key == "Self":
                    for cand in members_of(expect):
                        self_ty = cand
                        break
    if self_ty is None:
        raise err(29, self.m.file, line)
    target_name = self_ty.name if isinstance(self_ty, Nom) else self_ty.key
    prov = self.ck.provides.get((trait_fq, target_name))
    if prov is None:
        code = 97 if trait_fq == "core.convert.Json" else 98 if trait_fq == "core.convert.FromStr" else 82
        raise err(code, self.m.file, line)
    sig = prov.members.get(member)
    if self.pure and decl.effects:
        # S26: a trait with any task member is effectful, so fn code may not
        # call it. That is E084, not the plain fn-called-a-task E053.
        raise err(84, self.m.file, line)
    self.use_effect(decl.effects, line)
    if sig is None:
        # Toolchain provides for builtins have no source body; use the trait
        # signature with Self substituted (docs/stdlib.md core.convert).
        sig = Sig(
            kind=decl.kind,
            module=prov.module,
            name=member,
            symbol=f"provide:{trait_fq}:{target_name}:{member}",
            params=[(n, subst(t, {"Self": self_ty})) for n, t in decl.params],
            ret=subst(decl.ret, {"Self": self_ty}),
            effects=decl.effects,
            native=True,
            self_ty=self_ty,
            file=prov.file,
            line=prov.line,
        )
        self.ck.sigs.setdefault(sig.symbol, sig)
    ret = self.apply(sig, (), e.args, expect, line)
    if not self.generic:
        self.ck.request_func(sig.symbol, ())
    self.fc.res[id(e)] = CallRes(sig.symbol, (), sig.kind, sig.effects, ret)
    return ret


FnChecker.call = _fn_call
FnChecker.apply = _apply
FnChecker.trait_call = _trait_call
FnChecker.require_use = _require_use
FnChecker.last_targs = ()


# ---------------------------------------------------------------- checker glue

SECRET_SINKS = frozenset(
    {"core.log.write", "core.str.concat", "data.json.encode", "data.json.decode"}
)


def _has_secret(ck: Checker, ty: Ty, seen: set[str] | None = None) -> bool:
    seen = seen if seen is not None else set()
    if ty.key in seen:
        return False
    seen.add(ty.key)
    if isinstance(ty, Nom):
        if ty.name == "core.secret.Secret":
            return True
        info = ck.types.get(ty.name)
        if info is None:
            return False
        for _n, ft in info.field_types(ty.args):
            if _has_secret(ck, ft, seen):
                return True
        for i in range(len(info.variants)):
            for _n, ft in info.variant_fields(i, ty.args):
                if _has_secret(ck, ft, seen):
                    return True
        return False
    if isinstance(ty, Opt):
        return _has_secret(ck, ty.elem, seen)
    if isinstance(ty, ListT):
        return _has_secret(ck, ty.elem, seen)
    if isinstance(ty, MapT):
        return _has_secret(ck, ty.key_ty, seen) or _has_secret(ck, ty.val_ty, seen)
    if isinstance(ty, Union):
        return any(_has_secret(ck, m, seen) for m in ty.members)
    return False


def _kind_of(self: Checker, nom: Nom) -> str:
    info = self.types.get(nom.name)
    return info.kind if info else ""


def _is_json(self: Checker, ty: Ty) -> bool:
    if _has_secret(self, ty):
        return False
    if isinstance(ty, Prim):
        return ty.name in JSON_BUILTINS
    if isinstance(ty, Opt):
        return self.is_json(ty.elem)
    if isinstance(ty, ListT):
        return self.is_json(ty.elem)
    if isinstance(ty, MapT):
        return ty.key_ty.key == "str" and self.is_json(ty.val_ty)
    if isinstance(ty, Union):
        return all(self.is_json(m) for m in ty.members)
    if isinstance(ty, Nom):
        return ("core.convert.Json", ty.name) in self.provides
    return False


def _has_from_str(self: Checker, ty: Ty) -> bool:
    if isinstance(ty, Prim):
        return ty.name in FROM_STR_BUILTINS
    if isinstance(ty, Nom):
        return ("core.convert.FromStr", ty.name) in self.provides
    return False


def _request_type(self: Checker, nom: Nom) -> None:
    if nom.key in self.prog.noms:
        return
    self.prog.noms[nom.key] = nom
    info = self.types.get(nom.name)
    if info is None:
        raise err(20, "", 1)
    for _n, ft in info.field_types(nom.args):
        for sub in _noms_in(ft):
            self.request_type(sub)
    for i in range(len(info.variants)):
        for _n, ft in info.variant_fields(i, nom.args):
            for sub in _noms_in(ft):
                self.request_type(sub)


def _request_func(self: Checker, symbol: str, targs: tuple[Ty, ...]) -> None:
    key = inst_key(symbol, targs)
    vector = tuple(ty.key for ty in targs)
    for ancestor, arguments in self.current_instantiation:
        if ancestor == symbol and arguments != vector:
            sig = self.sigs[symbol]
            raise err(29, sig.file, sig.line)
    if key in self.prog.funcs or key in self.queued:
        return
    self.instantiation_paths[key] = self.current_instantiation + ((symbol, vector),)
    self.queued.add(key)
    self.pending.append((symbol, targs))


def _add_func(self: Checker, fc: FuncCheck) -> None:
    self.prog.funcs[fc.key] = fc
    self.queued.add(fc.key)


def _capture_type(
    self: Checker, fc: FuncCheck, index: int, names: list[str], tys: list[Ty]
) -> Nom:
    name = f"capture:{fc.key}:{index}"
    info = TypeInfo(kind="record", name=name, module=fc.module, public=False)
    info.fields = list(zip(names, tys))
    self.types[name] = info
    nom = Nom(name)
    self.request_type(nom)
    return nom


def _extra_effects(self: Checker, symbol: str) -> set[str]:
    return self.serve_extra.get(symbol, set())


def _guard_stdlib_call(
    self: Checker, fn: FnChecker, symbol: str, e: CallExpr, targs: tuple[Ty, ...], line: int
) -> None:
    module = fn.m.path
    if symbol in ("data.json.encode", "data.json.decode"):
        if targs and not self.is_json(targs[0]):
            raise err(97, fn.m.file, line)
    if symbol in ("net.http.request", "core.model.complete"):
        self.outbound.add(module)
        origins = self.egress.get(module)
        if not origins:
            raise err(111, fn.m.file, line)
        if symbol == "net.http.request":
            url = _literal_url(e.args[0]) if e.args else None
            arg = e.args[0] if e.args else None
            if url is None and isinstance(arg, NameExpr) and len(arg.parts) == 1:
                url = fn.literal_urls.get(arg.parts[0])
            if url is not None:
                got = normalize_origin(_origin_of(url) or "")
                allowed = {normalize_origin(o) for o in origins}
                if got is None or got not in allowed:
                    raise err(111, fn.m.file, line)
    if symbol == "net.http.serve":
        if fn.fc.symbol != f"{module}.serve" or not any(r.module == module for r in self.routes):
            raise err(93, fn.m.file, line)
    if symbol == "core.evolve.propose":
        ok = any(a.module == module and a.evolve_max is not None for a in self.agents)
        if not ok:
            raise err(115, fn.m.file, line)


def _literal_url(arg: object) -> str | None:
    if isinstance(arg, ConstructExpr):
        for name, value in arg.fields:
            if name == "url" and isinstance(value, StrLit):
                return _unquote(value.raw)
    return None


def _origin_of(url: str) -> str | None:
    from urllib.parse import urlsplit

    if "://" not in url or any(ord(ch) <= 32 or ord(ch) == 127 for ch in url):
        return None
    try:
        parsed = urlsplit(url)
        if not parsed.netloc or parsed.username is not None or parsed.password is not None:
            return None
        origin = f"{parsed.scheme}://{parsed.netloc}"
        return origin if normalize_origin(origin) is not None else None
    except ValueError:
        return None


def _check_functions(self: Checker) -> None:
    roots: list[tuple[str, tuple[Ty, ...]]] = []
    for symbol, sig in sorted(self.sigs.items()):
        if sig.native or sig.body is not None or not sig.contracts:
            continue
        FnChecker(self, sig, (), generic=bool(sig.tparams)).contracts_only()
    for symbol, sig in sorted(self.sigs.items()):
        if sig.native or sig.tparams:
            continue
        if sig.body is None:
            continue  # a spec callable with no impl is E033 (obligations phase)
        roots.append((symbol, ()))
    for m in self.pkg.tests.values():
        for t in m.tests:
            sig = Sig(
                kind="test",
                module=m.path,
                name=t.name,
                symbol=f"{m.path}.{t.name}",
                params=[],
                ret=UNIT,
                effects=frozenset(),
                body=t.body,
                line=t.line,
                file=m.file,
                mod=m,
                body_mod=m,
            )
            if sig.symbol in self.sigs:
                raise err(36, m.file, t.line)
            self.sigs[sig.symbol] = sig
            roots.append((sig.symbol, ()))
    for symbol, targs in roots:
        self.request_func(symbol, targs)
    while self.pending:
        symbol, targs = self.pending.pop(0)
        self.current_instantiation = self.instantiation_paths[inst_key(symbol, targs)]
        sig = self.sigs.get(symbol)
        if sig is None:
            raise err(74, "", 1)
        if sig.native or sig.body is None:
            env = sig.env(targs)
            self.add_func(
                FuncCheck(
                    symbol=symbol,
                    targs=targs,
                    kind="native",
                    module=sig.module,
                    file=sig.file,
                    params=[(n, subst(t, env)) for n, t in sig.params],
                    ret=subst(sig.ret, env),
                    effects=sig.effects,
                    contracts=list(sig.contracts),
                )
            )
            for _n, t in self.prog.funcs[inst_key(symbol, targs)].params:
                for nom in _noms_in(t):
                    self.request_type(nom)
            for nom in _noms_in(self.prog.funcs[inst_key(symbol, targs)].ret):
                self.request_type(nom)
            continue
        fc = FnChecker(self, sig, targs).run()
        for _n, t in fc.params:
            for nom in _noms_in(t):
                self.request_type(nom)
        for nom in _noms_in(fc.ret):
            self.request_type(nom)
        for t in fc.local_types:
            for nom in _noms_in(t):
                self.request_type(nom)
        self.add_func(fc)
    self.current_instantiation = ()
    # Generic declarations nobody instantiated are still checked once, so a
    # broken body cannot hide behind "no call site" (docs/implementer.md 4).
    for symbol, sig in sorted(self.sigs.items()):
        if not sig.tparams or sig.native or sig.body is None:
            continue
        if any(key == symbol or key.startswith(symbol + "[") for key in self.prog.funcs):
            continue
        FnChecker(self, sig, (), generic=True).run()


def _check_obligations(self: Checker) -> None:
    for symbol, sig in sorted(self.sigs.items()):
        for c in sig.contracts:
            if c.open_id is None:
                continue
            if not c.open_id.startswith("needs_test_") or sig.kind != "fn":
                raise err(65, sig.file, c.line)
            if symbol not in self.public:
                raise err(65, sig.file, c.line)
            want = c.open_id[len("needs_test_") :]
            tests = self.pkg.tests.get("test." + sig.module)
            hits = [t for t in (tests.tests if tests else []) if t.name == want]
            if len(hits) != 1:
                if not hits:
                    from gopyt.elaborate import test_stub
                    spec = self.pkg.spec[sig.module]
                    path = "test/" + sig.module.replace(".", "/") + ".gopyt"
                    raise err(65, path, 1, repair=test_stub(spec, tests))
                raise err(65, sig.file, c.line)


def _check_unused_uses(self: Checker) -> None:
    for m in self.all_modules():
        if m.role == "stdlib":
            continue
        for u in m.uses:
            for name in u.names:
                if (m.file, u.path, name) not in self.used_names:
                    raise err(14, m.file, u.line)


Checker.kind_of = _kind_of
Checker.is_json = _is_json
Checker.has_from_str = _has_from_str
Checker.request_type = _request_type
Checker.request_func = _request_func
Checker.add_func = _add_func
Checker.capture_type = _capture_type
Checker.extra_effects = _extra_effects
Checker.guard_stdlib_call = _guard_stdlib_call
Checker.check_functions = _check_functions
Checker.check_obligations = _check_obligations
Checker.check_unused_uses = _check_unused_uses


# ---------------------------------------------------------------- entry points


def check_formatted(pkg: Pkg) -> None:
    for group in (pkg.spec, pkg.impl, pkg.tests, pkg.dep_specs, pkg.dep_impls):
        for m in group.values():
            src = m.source
            want = fmt_module(m)
            if src != want:
                raise err(4, m.file, 1, repair=want)


def check_lock(pkg: Pkg) -> None:
    want = lock_text(pkg.packages)
    path = os.path.join(pkg.root, "gopyt.lock")
    if not os.path.isfile(path):
        raise err(41, "gopyt.lock", 1, repair=want)
    with regular_file(pkg.root, "gopyt.lock") as fh:
        got = fh.read().decode("utf-8")
    if got.split("\n", 1)[0] != want.split("\n", 1)[0]:
        raise err(40, "gopyt.lock", 1, repair=want)
    if got != want:
        raise err(41, "gopyt.lock", 1, repair=want)


def check_package(pkg: Pkg, check_fmt: bool = True, check_lockfile: bool = True) -> Program:
    ck = Checker(pkg)
    ck.collect()
    ck.resolve_bodies_of_types()
    # Derived provides must exist even when only the HTTP dispatcher calls
    # them: there may be no source trait call to request a specialization.
    for (trait_name, target_name), prov in ck.provides.items():
        target = prov.target
        if isinstance(target, Nom):
            info = ck.types[target.name]
            fields = info.field_types(target.args) if info.kind == "record" else []
            derived = trait_name == "core.convert.Json" or (
                trait_name == "core.convert.FromStr" and len(fields) == 1 and fields[0][1] in (STR, I64))
        else:
            derived = False
        if not derived:
            continue
        if prov.members:
            raise err(97 if trait_name == "core.convert.Json" else 98, prov.file, prov.line)
        for member in ck.traits[trait_name].members:
            symbol = f"provide:{trait_name}:{target_name}:{member.name}"
            ck.sigs.setdefault(symbol, Sig(kind=member.kind, module=prov.module,
                name=member.name, symbol=symbol,
                params=[(name, subst(ty, {"Self": target})) for name, ty in member.params],
                ret=subst(member.ret, {"Self": target}), effects=member.effects,
                native=True, self_ty=target, file=prov.file, line=prov.line))
    decls = DeclChecker(ck)
    decls.run()
    ck.check_obligations()
    ck.check_functions()
    decls.obligations()
    decls.unused_egress()
    ck.check_unused_uses()
    if check_fmt:
        check_formatted(pkg)
    if check_lockfile:
        check_lock(pkg)
    ck.prog.types = ck.types
    ck.prog.root_module_prefix = pkg.manifest.name if pkg.manifest else ""
    for r in ck.routes:
        handler = inst_key(f"{r.module}.{r.handler}", ())
        ck.prog.routes.append((r.module, r.verb, r.path, handler))
    for agent in ck.agents:
        if agent.evolve_max is not None:
            bounds = (agent.evolve_max, agent.evolve_timeout, agent.evolve_reservoir)
            if agent.module in ck.prog.evolve and ck.prog.evolve[agent.module] != bounds:
                raise err(117, agent.file, agent.line)
            ck.prog.evolve[agent.module] = (
                agent.evolve_max,
                agent.evolve_timeout,
                agent.evolve_reservoir,
            )
    for module in sorted(ck.egress):
        for origin in sorted(ck.egress[module]):
            ck.prog.egress.append((module, normalize_origin(origin) or origin))
    return ck.prog
