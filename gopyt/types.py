"""Canonical v0 type model.

Follows docs/spec.md S6-S8 (builtins, optionals, unions, no holes) and the
TypeExpr encoding of docs/bytecode.md. Types are compared through `key`, the
single canonical spelling, so a type is never two things at once.
"""

from __future__ import annotations

from dataclasses import dataclass

PRIMS = ("bool", "i32", "i64", "u32", "u64", "f64", "str", "bytes", "unit")
MAP_KEYS = ("str", "i64", "bool")
TYPE_VARS = ("T", "K", "V")
# S20: equality is structural for these; f64 and opaque handles are excluded.
EQ_PRIMS = ("bool", "i32", "i64", "u32", "u64", "str", "bytes", "unit")


class Ty:
    __slots__ = ()

    @property
    def key(self) -> str:
        raise NotImplementedError

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Ty) and self.key == other.key

    def __hash__(self) -> int:
        return hash(self.key)

    def __repr__(self) -> str:
        return self.key


@dataclass(frozen=True, eq=False)
class Prim(Ty):
    name: str

    @property
    def key(self) -> str:
        return self.name


@dataclass(frozen=True, eq=False)
class Opt(Ty):
    elem: Ty

    @property
    def key(self) -> str:
        return self.elem.key + "?"


@dataclass(frozen=True, eq=False)
class ListT(Ty):
    elem: Ty

    @property
    def key(self) -> str:
        return f"list[{self.elem.key}]"


@dataclass(frozen=True, eq=False)
class MapT(Ty):
    key_ty: Ty
    val_ty: Ty

    @property
    def key(self) -> str:
        return f"map[{self.key_ty.key}, {self.val_ty.key}]"


@dataclass(frozen=True, eq=False)
class Nom(Ty):
    """A nominal record, enum, or toolchain-opaque type, fully qualified."""

    name: str
    args: tuple[Ty, ...] = ()

    @property
    def key(self) -> str:
        if not self.args:
            return self.name
        return self.name + "[" + ", ".join(a.key for a in self.args) + "]"


@dataclass(frozen=True, eq=False)
class Union(Ty):
    """Two or more distinct nominal members, kept in source order (bytecode.md)."""

    members: tuple[Ty, ...]

    @property
    def key(self) -> str:
        return " | ".join(sorted(m.key for m in self.members))

    @property
    def source_key(self) -> str:
        return " | ".join(m.key for m in self.members)


@dataclass(frozen=True, eq=False)
class Var(Ty):
    name: str

    @property
    def key(self) -> str:
        return self.name


class NoneTy(Ty):
    """Type of the `none` literal; injects into any optional."""

    __slots__ = ()

    @property
    def key(self) -> str:
        return "none"


NONE = NoneTy()
BOOL = Prim("bool")
I64 = Prim("i64")
STR = Prim("str")
BYTES = Prim("bytes")
UNIT = Prim("unit")
F64 = Prim("f64")


def is_var(t: Ty) -> bool:
    return isinstance(t, Var)


def has_var(t: Ty) -> bool:
    if isinstance(t, Var):
        return True
    if isinstance(t, Opt):
        return has_var(t.elem)
    if isinstance(t, ListT):
        return has_var(t.elem)
    if isinstance(t, MapT):
        return has_var(t.key_ty) or has_var(t.val_ty)
    if isinstance(t, Nom):
        return any(has_var(a) for a in t.args)
    if isinstance(t, Union):
        return any(has_var(m) for m in t.members)
    return False


def subst(t: Ty, env: dict[str, Ty]) -> Ty:
    if isinstance(t, Var):
        return env.get(t.name, t)
    if isinstance(t, Opt):
        return Opt(subst(t.elem, env))
    if isinstance(t, ListT):
        return ListT(subst(t.elem, env))
    if isinstance(t, MapT):
        return MapT(subst(t.key_ty, env), subst(t.val_ty, env))
    if isinstance(t, Nom):
        return Nom(t.name, tuple(subst(a, env) for a in t.args))
    if isinstance(t, Union):
        return Union(tuple(subst(m, env) for m in t.members))
    return t


def unify(param: Ty, arg: Ty, env: dict[str, Ty]) -> bool:
    """One-way match of a declared parameter type against a concrete argument."""
    if isinstance(param, Var):
        bound = env.get(param.name)
        if bound is None:
            if isinstance(arg, NoneTy):
                return False
            env[param.name] = arg
            return True
        return bound.key == arg.key
    if isinstance(arg, NoneTy):
        return isinstance(param, Opt)
    if isinstance(param, Opt) and isinstance(arg, Opt):
        return unify(param.elem, arg.elem, env)
    if isinstance(param, ListT) and isinstance(arg, ListT):
        return unify(param.elem, arg.elem, env)
    if isinstance(param, MapT) and isinstance(arg, MapT):
        return unify(param.key_ty, arg.key_ty, env) and unify(param.val_ty, arg.val_ty, env)
    if isinstance(param, Nom) and isinstance(arg, Nom):
        if param.name != arg.name or len(param.args) != len(arg.args):
            return False
        return all(unify(p, a, env) for p, a in zip(param.args, arg.args))
    if isinstance(param, Union) and isinstance(arg, Union):
        if len(param.members) != len(arg.members):
            return False
        return all(unify(p, a, env) for p, a in zip(param.members, arg.members))
    return param.key == arg.key


def members_of(t: Ty) -> tuple[Ty, ...]:
    return t.members if isinstance(t, Union) else (t,)


def assignable(got: Ty, want: Ty) -> bool:
    """S7/S8: no implicit conversions; a member injects into its union."""
    if got.key == want.key:
        return True
    if isinstance(got, NoneTy):
        return isinstance(want, Opt)
    if isinstance(want, Union):
        wanted = {m.key for m in want.members}
        return all(m.key in wanted for m in members_of(got))
    return False


def supports_eq(t: Ty, lookup, seen: frozenset[str] = frozenset()) -> bool:
    """S20 / implementer.md 1: structural equality, recursively, never f64."""
    if isinstance(t, Prim):
        return t.name in EQ_PRIMS
    if isinstance(t, NoneTy):
        return True
    if isinstance(t, Opt):
        return supports_eq(t.elem, lookup, seen)
    if isinstance(t, ListT):
        return supports_eq(t.elem, lookup, seen)
    if isinstance(t, MapT):
        return supports_eq(t.key_ty, lookup, seen) and supports_eq(t.val_ty, lookup, seen)
    if isinstance(t, Union):
        return all(supports_eq(m, lookup, seen) for m in t.members)
    if isinstance(t, Nom):
        info = lookup(t)
        if info is None or info.kind == "opaque":
            return False
        if t.key in seen:
            return True
        seen = seen | {t.key}
        fields = info.field_types(t.args)
        for index in range(len(info.variants)):
            fields += info.variant_fields(index, t.args)
        for _n, ft in fields:
            if not supports_eq(ft, lookup, seen):
                return False
        return True
    return False
