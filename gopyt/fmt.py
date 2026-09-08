from __future__ import annotations

from gopyt.ast_nodes import *
from gopyt.parser import EFFECTS


def lead(node: object, indent: int = 0) -> list[str]:
    """Own-line comments above a construct (docs/fmt.md: attachment kept)."""
    pad = " " * indent
    return [f"{pad}//{text}" for text in getattr(node, "comments", []) or []]


def trail(node: object, text: str) -> str:
    """A `// ...` that followed the construct on its own line."""
    comment = getattr(node, "trailing", None)
    return f"{text} //{comment}" if comment else text


def fmt_module(m: Module) -> str:
    lines = [f"module {m.path}", ""]
    uses = sorted(m.uses, key=lambda u: u.path.encode("utf-8"))
    for u in uses:
        pas = sorted([n for n in u.names if n[:1].isupper()], key=lambda x: x.encode())
        sna = sorted([n for n in u.names if not n[:1].isupper()], key=lambda x: x.encode())
        names = pas + sna
        lines.extend(lead(u))
        lines.append(trail(u, f"use {u.path} {{ {', '.join(names)} }}"))
    if uses:
        lines.append("")
    chunks = []
    if m.role == "test":
        for t in m.tests:
            chunks.append("\n".join(lead(t) + [trail(t, fmt_test(t))]))
    else:
        for it in m.items:
            text = fmt_item(it)
            if text:
                chunks.append("\n".join(lead(it) + [trail(it, text)]))
    body = "\n\n".join(c for c in chunks if c)
    if body:
        lines.append(body)
    if m.comments:
        lines.append("")
        lines.extend(f"//{text}" for text in m.comments)
    text = "\n".join(lines).rstrip() + "\n"
    return text


def fmt_item(it: object) -> str:
    if isinstance(it, TypeDecl):
        return fmt_type(it)
    if isinstance(it, EnumDecl):
        return fmt_enum(it)
    if isinstance(it, TraitDecl):
        return fmt_trait(it)
    if isinstance(it, ProvideDecl):
        return fmt_provide(it)
    if isinstance(it, FnDecl):
        return fmt_fn(it)
    if isinstance(it, AgentDecl):
        return fmt_agent(it)
    if isinstance(it, HttpDecl):
        return fmt_http(it)
    if isinstance(it, EgressDecl):
        return fmt_egress(it)
    return ""


def fmt_type_expr(t: TypeExpr) -> str:
    return " | ".join(fmt_named(p) for p in t.parts)


def fmt_named(n: NamedType) -> str:
    s = n.name
    if n.args:
        s += "[" + ", ".join(fmt_type_expr(a) for a in n.args) + "]"
    if n.optional:
        s += "?"
    return s


def fmt_fields(fields: list[Field], indent: int) -> str:
    pad = " " * indent
    out = []
    for f in fields:
        out.extend(f"{line}\n" for line in lead(f, indent))
        out.append(trail(f, f"{pad}{f.name}: {fmt_type_expr(f.ty)}") + "\n")
    return "{\n" + "".join(out) + (" " * (indent - 4)) + "}"


def fmt_type(d: TypeDecl) -> str:
    tp = f"[{', '.join(d.tparams)}]" if d.tparams else ""
    return f"type {d.name}{tp} {fmt_fields(d.fields, 4)}"


def fmt_enum(d: EnumDecl) -> str:
    lines = [f"enum {d.name} {{"]
    for v in d.variants:
        lines.extend(lead(v, 4))
        if v.fields:
            lines.append(trail(v, f"    {v.name} {fmt_fields(v.fields, 8)}"))
        else:
            lines.append(trail(v, f"    {v.name}"))
    lines.append("}")
    return "\n".join(lines)


def fmt_sig(s: FnSig) -> str:
    tp = f"[{', '.join(s.tparams)}]" if s.tparams else ""
    ps = ", ".join(f"{p.name}: {fmt_type_expr(p.ty)}" for p in s.params)
    out = f"{s.kind} {s.name}{tp}({ps}) -> {fmt_type_expr(s.ret)}"
    if s.kind in ("task", "workflow"):
        eff = [e for e in EFFECTS if e in s.effects]
        extra = [e for e in s.effects if e not in EFFECTS]
        ordered = eff + extra
        out += "\n    effects { " + ", ".join(ordered) + " }"
    for c in s.contracts:
        if c.open_id:
            out += f"\n    {c.kind} open {c.open_id}"
        else:
            out += f"\n    {c.kind} {fmt_expr(c.expr, 4)}"
    return out


def fmt_fn(d: FnDecl) -> str:
    s = fmt_sig(d.sig)
    if d.body is None:
        return s
    return s + "\n" + fmt_block(d.body, 0)


def fmt_block(b: Block, indent: int) -> str:
    """Body braces on their own lines (docs/fmt.md)."""
    pad = " " * indent
    lines = [pad + "{"]
    for st in b.stmts:
        lines.extend(lead(st, indent + 4))
        lines.append(trail(st, fmt_stmt(st, indent + 4)))
    lines.extend(lead(b, indent + 4))
    lines.append(pad + "}")
    return "\n".join(lines)


def fmt_block_same(b: Block, indent: int) -> str:
    """Header brace stays on the header line (if / for / match / parallel)."""
    pad = " " * indent
    lines = ["{"]
    for st in b.stmts:
        lines.extend(lead(st, indent + 4))
        lines.append(trail(st, fmt_stmt(st, indent + 4)))
    lines.extend(lead(b, indent + 4))
    lines.append(pad + "}")
    return "\n".join(lines)


def fmt_stmt(st: object, indent: int) -> str:
    pad = " " * indent
    if isinstance(st, ReturnStmt):
        return f"{pad}return {fmt_expr(st.expr, indent)}"
    if isinstance(st, BindStmt):
        return f"{pad}{st.name} = {fmt_expr(st.expr, indent)}"
    if isinstance(st, UnresolvedStmt):
        return f"{pad}unresolved {st.name}"
    if isinstance(st, ExprStmt):
        return f"{pad}{fmt_expr(st.expr, indent)}"
    if isinstance(st, IfStmt):
        out = f"{pad}if {fmt_expr(st.cond, indent)} {fmt_block_same(st.then, indent)}"
        if st.els:
            out += f" else {fmt_block_same(st.els, indent)}"
        return out
    if isinstance(st, ForStmt):
        return (
            f"{pad}for {st.name} in {fmt_expr(st.iter, indent)} "
            f"{fmt_block_same(st.body, indent)}"
        )
    return pad + "?"


# docs/grammar.ebnf precedence, loosest first. A child printed at a level below
# what its position requires must keep its parentheses, or the formatter would
# change what the program means.
_LEVEL_OR = 1
_LEVEL_AND = 2
_LEVEL_NOT = 3
_LEVEL_CMP = 4
_LEVEL_ADD = 5
_LEVEL_MUL = 6
_LEVEL_NEG = 7
_LEVEL_PRIMARY = 8

_BIN_LEVEL = {
    "or": _LEVEL_OR,
    "and": _LEVEL_AND,
    "==": _LEVEL_CMP,
    "!=": _LEVEL_CMP,
    "<": _LEVEL_CMP,
    ">": _LEVEL_CMP,
    "<=": _LEVEL_CMP,
    ">=": _LEVEL_CMP,
    "+": _LEVEL_ADD,
    "-": _LEVEL_ADD,
    "*": _LEVEL_MUL,
    "/": _LEVEL_MUL,
    "%": _LEVEL_MUL,
}


def level(e: object) -> int:
    if isinstance(e, BinExpr):
        return _BIN_LEVEL.get(e.op, _LEVEL_PRIMARY)
    if isinstance(e, UnaryExpr):
        return _LEVEL_NOT if e.op == "not" else _LEVEL_NEG
    return _LEVEL_PRIMARY


def sub_expr(e: object, need: int, indent: int) -> str:
    """Format `e` where the grammar admits nothing looser than `need`."""
    text = fmt_expr(e, indent)
    return f"({text})" if level(e) < need else text


def fmt_expr(e: object, indent: int = 0) -> str:
    pad = " " * indent
    inner = " " * (indent + 4)
    if isinstance(e, IntLit):
        return e.value
    if isinstance(e, FloatLit):
        return e.value
    if isinstance(e, StrLit):
        return e.raw
    if isinstance(e, BoolLit):
        return "true" if e.value else "false"
    if isinstance(e, NoneLit):
        return "none"
    if isinstance(e, UnitLit):
        return "unit"
    if isinstance(e, SomeExpr):
        return f"some({fmt_expr(e.inner, indent)})"
    if isinstance(e, NameExpr):
        return ".".join(e.parts)
    if isinstance(e, FieldExpr):
        return f"{fmt_expr(e.base, indent)}.{e.name}"
    if isinstance(e, CallExpr):
        cal = fmt_expr(e.callee, indent)
        ta = ""
        if e.targs:
            ta = "[" + ", ".join(fmt_type_expr(t) for t in e.targs) + "]"
        args = ", ".join(fmt_expr(a, indent) for a in e.args)
        return f"{cal}{ta}({args})"
    if isinstance(e, ConstructExpr):
        if not e.fields:
            return e.ty if e.enum else f"{e.ty} {{}}"
        if len(e.fields) == 1:
            name, value = e.fields[0]
            return f"{e.ty} {{ {name}: {fmt_expr(value, indent)} }}"
        body = "".join(f"{inner}{n}: {fmt_expr(v, indent + 4)}\n" for n, v in e.fields)
        return f"{e.ty} {{\n{body}{pad}}}"
    if isinstance(e, BinExpr):
        own = _BIN_LEVEL.get(e.op, _LEVEL_PRIMARY)
        # Every binary operator is left-associative or non-associative, so the
        # right operand needs one level tighter than the left.
        left = sub_expr(e.left, own, indent)
        right = sub_expr(e.right, own + 1, indent)
        return f"{left} {e.op} {right}"
    if isinstance(e, UnaryExpr):
        if e.op == "not":
            # NotExpr takes a CmpExpr, so anything looser needs parentheses.
            return f"not {sub_expr(e.inner, _LEVEL_CMP, indent)}"
        return f"-{sub_expr(e.inner, _LEVEL_PRIMARY, indent)}"
    if isinstance(e, MatchExpr):
        lines = [f"match {fmt_expr(e.scrut, indent)} {{"]
        for a in e.arms:
            lines.extend(lead(a, indent + 4))
            lines.append(
                trail(a, f"{inner}{fmt_pat(a.pattern)} -> {fmt_expr(a.body, indent + 4)}")
            )
        lines.append(pad + "}")
        return "\n".join(lines)
    if isinstance(e, ParallelExpr):
        lines = [f"parallel max {e.max_n} timeout_ms {e.timeout_ms} {{"]
        for a in e.arms:
            lines.append(f"{inner}{fmt_expr(a, indent + 4)}")
        lines.append(pad + "}")
        return "\n".join(lines)
    return "?"


def fmt_pat(p: object) -> str:
    if isinstance(p, PatNone):
        return "none"
    if isinstance(p, PatSome):
        return f"some({p.name})"
    if isinstance(p, PatType):
        if p.binds:
            return p.name + " { " + " ".join(p.binds) + " }"
        return p.name
    return "?"


def fmt_trait(d: TraitDecl) -> str:
    tp = f"[{', '.join(d.tparams)}]" if d.tparams else ""
    lines = [f"trait {d.name}{tp} {{"]
    for mem in d.members:
        for ln in fmt_sig(mem).split("\n"):
            lines.append("    " + ln)
    lines.append("}")
    return "\n".join(lines)


def fmt_provide(d: ProvideDecl) -> str:
    ta = ""
    if d.trait_args:
        ta = "[" + ", ".join(fmt_type_expr(a) for a in d.trait_args) + "]"
    tg = ""
    if d.target_args:
        tg = "[" + ", ".join(fmt_type_expr(a) for a in d.target_args) + "]"
    s = f"provide {d.trait}{ta} for {d.target}{tg}"
    if d.members is None:
        return s
    lines = [s, "{"]
    for mem in d.members:
        for ln in fmt_fn(mem).split("\n"):
            lines.append("    " + ln)
    lines.append("}")
    return "\n".join(lines)


def fmt_agent(d: AgentDecl) -> str:
    eff = [e for e in EFFECTS if e in d.effects]
    tasks = ", ".join(sorted(d.tasks, key=lambda x: x.encode()))
    s = f"agent {d.name}\n    effects {{ {', '.join(eff)} }}\n    tasks {{ {tasks} }}"
    if d.evolve_max is not None:
        s += (
            f"\n    evolve {{\n        max {d.evolve_max}\n        timeout_ms {d.evolve_timeout}\n"
            f"        reservoir {d.evolve_reservoir}\n    }}"
        )
    return s


def fmt_http(d: HttpDecl) -> str:
    lines = ["http {"]
    for r in d.routes:
        lines.append(f"    {r.verb} {r.path} {r.handler}")
    lines.append("}")
    return "\n".join(lines)


def fmt_egress(d: EgressDecl) -> str:
    orig = ", ".join(sorted(d.origins, key=lambda x: x.encode()))
    return f"egress {{ {orig} }}"


def fmt_test(t: TestDecl) -> str:
    return f"test {t.name}\n{fmt_block(t.body, 0)}"
