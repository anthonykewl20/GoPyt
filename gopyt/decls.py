"""Declaration tables shared by the checker and the emitter.

Nothing here invents a name: every field mirrors a construct that
docs/spec.md, docs/stdlib.md, or docs/implementer.md already fixes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gopyt.types import Nom, Ty, subst


@dataclass
class TypeInfo:
    kind: str  # record | enum | opaque
    name: str  # fully qualified
    module: str
    tparams: tuple[str, ...] = ()
    fields: list[tuple[str, Ty]] = field(default_factory=list)
    variants: list[tuple[str, list[tuple[str, Ty]]]] = field(default_factory=list)
    line: int = 1
    file: str = ""
    public: bool = True

    def env(self, args: tuple[Ty, ...]) -> dict[str, Ty]:
        return {n: a for n, a in zip(self.tparams, args)}

    def field_types(self, args: tuple[Ty, ...] = ()) -> list[tuple[str, Ty]]:
        env = self.env(args)
        return [(n, subst(t, env)) for n, t in self.fields]

    def variant_index(self, name: str) -> int:
        for i, (vn, _f) in enumerate(self.variants):
            if vn == name:
                return i
        return -1

    def variant_fields(self, index: int, args: tuple[Ty, ...] = ()) -> list[tuple[str, Ty]]:
        env = self.env(args)
        return [(n, subst(t, env)) for n, t in self.variants[index][1]]


@dataclass
class Sig:
    """A declared callable: fn, task, workflow, trait member, or test."""

    kind: str  # fn | task | workflow | test
    module: str
    name: str
    symbol: str
    tparams: tuple[str, ...] = ()
    params: list[tuple[str, Ty]] = field(default_factory=list)
    ret: Ty | None = None
    effects: frozenset[str] = frozenset()
    contracts: list = field(default_factory=list)
    body: object | None = None
    native: bool = False
    line: int = 1
    file: str = ""
    self_ty: Ty | None = None  # provide members: what Self means
    mod: object = None  # declaring Module
    body_mod: object = None  # Module whose `use` list the body resolves against

    def env(self, args: tuple[Ty, ...]) -> dict[str, Ty]:
        env = {n: a for n, a in zip(self.tparams, args)}
        if self.self_ty is not None:
            env["Self"] = self.self_ty
        return env


@dataclass
class TraitInfo:
    name: str  # fully qualified
    module: str
    tparams: tuple[str, ...]
    members: list[Sig]
    line: int = 1
    file: str = ""

    def member(self, name: str) -> Sig | None:
        for m in self.members:
            if m.name == name:
                return m
        return None

    @property
    def effectful(self) -> bool:
        return any(m.kind == "task" for m in self.members)


@dataclass
class ProvideInfo:
    trait: str  # fully qualified trait name
    target: Nom
    module: str
    members: dict[str, Sig] = field(default_factory=dict)
    line: int = 1
    file: str = ""


@dataclass
class AgentInfo:
    name: str
    module: str
    effects: frozenset[str]
    tasks: tuple[str, ...]
    evolve_max: int | None = None
    evolve_timeout: int | None = None
    evolve_reservoir: int | None = None
    line: int = 1
    file: str = ""


@dataclass
class RouteInfo:
    module: str
    verb: str
    path: str
    handler: str
    line: int = 1
    file: str = ""
