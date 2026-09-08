from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Node:
    line: int = 1
    comments: list[str] = field(default_factory=list)  # own-line comments above
    trailing: str | None = None  # `// ...` after this node on the same line


@dataclass
class TypeExpr(Node):
    parts: list[NamedType] = field(default_factory=list)  # union of named


@dataclass
class NamedType(Node):
    name: str = ""  # builtin, Self, T, Pascal, or module.Pascal
    args: list[TypeExpr] = field(default_factory=list)
    optional: bool = False


@dataclass
class Param(Node):
    name: str = ""
    ty: TypeExpr | None = None


@dataclass
class Contract(Node):
    kind: str = "requires"  # requires|ensures
    expr: object | None = None
    open_id: str | None = None


@dataclass
class FnSig(Node):
    kind: str = "fn"  # fn|task|workflow
    name: str = ""
    tparams: list[str] = field(default_factory=list)
    params: list[Param] = field(default_factory=list)
    ret: TypeExpr | None = None
    effects: list[str] = field(default_factory=list)
    contracts: list[Contract] = field(default_factory=list)


@dataclass
class Block(Node):
    stmts: list[object] = field(default_factory=list)


@dataclass
class FnDecl(Node):
    sig: FnSig | None = None
    body: Block | None = None


@dataclass
class Field(Node):
    name: str = ""
    ty: TypeExpr | None = None


@dataclass
class TypeDecl(Node):
    name: str = ""
    tparams: list[str] = field(default_factory=list)
    fields: list[Field] = field(default_factory=list)


@dataclass
class Variant(Node):
    name: str = ""
    fields: list[Field] = field(default_factory=list)


@dataclass
class EnumDecl(Node):
    name: str = ""
    variants: list[Variant] = field(default_factory=list)


@dataclass
class TraitDecl(Node):
    name: str = ""
    tparams: list[str] = field(default_factory=list)
    members: list[FnSig] = field(default_factory=list)


@dataclass
class ProvideDecl(Node):
    trait: str = ""
    trait_args: list[TypeExpr] = field(default_factory=list)
    target: str = ""
    target_args: list[TypeExpr] = field(default_factory=list)
    members: list[FnDecl] | None = None


@dataclass
class AgentDecl(Node):
    name: str = ""
    effects: list[str] = field(default_factory=list)
    tasks: list[str] = field(default_factory=list)
    evolve_max: int | None = None
    evolve_timeout: int | None = None
    evolve_reservoir: int | None = None


@dataclass
class HttpRoute(Node):
    verb: str = ""
    path: str = ""
    handler: str = ""


@dataclass
class HttpDecl(Node):
    routes: list[HttpRoute] = field(default_factory=list)


@dataclass
class EgressDecl(Node):
    origins: list[str] = field(default_factory=list)


@dataclass
class UseDecl(Node):
    path: str = ""
    names: list[str] = field(default_factory=list)


@dataclass
class TestDecl(Node):
    name: str = ""
    body: Block | None = None


@dataclass
class Module(Node):
    path: str = ""
    uses: list[UseDecl] = field(default_factory=list)
    items: list[object] = field(default_factory=list)
    tests: list[TestDecl] = field(default_factory=list)
    role: str = "spec"  # spec|impl|test
    file: str = ""
    source: str = ""  # exact bytes as parsed, for token-level checks


# expressions / statements
@dataclass
class IntLit(Node):
    value: str = "0"


@dataclass
class FloatLit(Node):
    value: str = "0.0"


@dataclass
class StrLit(Node):
    raw: str = '""'


@dataclass
class BoolLit(Node):
    value: bool = False


@dataclass
class NoneLit(Node):
    pass


@dataclass
class UnitLit(Node):
    pass


@dataclass
class SomeExpr(Node):
    inner: object = None


@dataclass
class NameExpr(Node):
    parts: list[str] = field(default_factory=list)


@dataclass
class FieldExpr(Node):
    base: object = None
    name: str = ""


@dataclass
class CallExpr(Node):
    callee: object = None
    targs: list[TypeExpr] = field(default_factory=list)
    args: list[object] = field(default_factory=list)


@dataclass
class ConstructExpr(Node):
    ty: str = ""
    fields: list[tuple[str, object]] = field(default_factory=list)
    enum: bool = False


@dataclass
class BinExpr(Node):
    op: str = "+"
    left: object = None
    right: object = None


@dataclass
class UnaryExpr(Node):
    op: str = "-"
    inner: object = None


@dataclass
class MatchArm(Node):
    pattern: object = None
    body: object = None


@dataclass
class MatchExpr(Node):
    scrut: object = None
    arms: list[MatchArm] = field(default_factory=list)


@dataclass
class PatNone(Node):
    pass


@dataclass
class PatSome(Node):
    name: str = ""


@dataclass
class PatType(Node):
    name: str = ""
    binds: list[str] = field(default_factory=list)


@dataclass
class ParallelExpr(Node):
    max_n: int = 1
    timeout_ms: int = 1
    arms: list[object] = field(default_factory=list)


@dataclass
class BindStmt(Node):
    name: str = ""
    expr: object = None


@dataclass
class ReturnStmt(Node):
    expr: object = None


@dataclass
class IfStmt(Node):
    cond: object = None
    then: Block | None = None
    els: Block | None = None


@dataclass
class ForStmt(Node):
    name: str = ""
    iter: object = None
    body: Block | None = None


@dataclass
class ExprStmt(Node):
    expr: object = None


@dataclass
class UnresolvedStmt(Node):
    name: str = ""
