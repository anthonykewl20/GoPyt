from __future__ import annotations

from gopyt.ast_nodes import *
from gopyt.diag import CompileError, Diag
from gopyt.lexer import Lexer, Tok

EFFECTS = [
    "network",
    "filesystem.read",
    "filesystem.write",
    "database.read",
    "database.write",
    "time",
    "random",
    "log",
    "model",
    "ffi",
    "secret",
    "observe",
    "resource",
]


# Keywords that are still legal `snake` spellings in a name position. The closed
# stdlib itself needs them: `core.list`, `store.db.put`, `core.random.i64_in(max:
# i64)`. They keep their keyword meaning in every syntactic position that uses
# them (docs/grammar.ebnf: verbs are "only meaningful in http block").
SOFT_KEYWORDS = {
    "bool",
    "i32",
    "i64",
    "u32",
    "u64",
    "f64",
    "str",
    "bytes",
    "list",
    "map",
    "unit",
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "test",
    "http",
    "evolve",
    "tasks",
    "max",
    "timeout_ms",
    "reservoir",
}


def is_name_tok(tok: Tok) -> bool:
    """True for a token that may spell a snake identifier."""
    return tok.kind == "IDENT" or (tok.kind == "KW" and tok.val in SOFT_KEYWORDS)


class Parser:
    def __init__(self, src: str, file: str, role: str) -> None:
        self.file = file
        self.role = role
        self.lex = Lexer(src, file)
        self.toks = self.lex.tokenize()
        self.i = 0
        self.depth = 0
        self.pending = list(self.lex.comments)
        self.ci = 0

    def t(self) -> Tok:
        return self.toks[self.i]

    def at(self, kind: str, val: str | None = None) -> bool:
        tok = self.t()
        if val is None:
            return tok.kind == kind or tok.val == kind
        return tok.kind == kind and tok.val == val

    def kw(self, word: str) -> bool:
        return self.t().kind == "KW" and self.t().val == word

    def eat_kw(self, word: str) -> Tok:
        if not self.kw(word):
            raise self.err(11)
        return self.bump()

    def eat(self, kind: str) -> Tok:
        if self.t().kind != kind and self.t().val != kind:
            raise self.err(11)
        return self.bump()

    def bump(self) -> Tok:
        tok = self.t()
        self.i += 1
        return tok

    # -- comments (docs/fmt.md: text and attachment are preserved) ---------

    def take_leading(self, line: int) -> list[str]:
        """Own-line comments that sit above the construct starting at `line`."""
        out: list[str] = []
        while self.ci < len(self.pending):
            cline, text, own = self.pending[self.ci]
            if not own or cline >= line:
                break
            out.append(text)
            self.ci += 1
        return out

    def take_trailing(self) -> str | None:
        """A comment after the last consumed token, on that same line."""
        if self.i == 0:
            return None
        line = self.toks[self.i - 1].line
        if self.ci < len(self.pending):
            cline, text, own = self.pending[self.ci]
            if not own and cline == line:
                self.ci += 1
                return text
        return None

    def rest_comments(self) -> list[str]:
        out = [text for _l, text, _o in self.pending[self.ci :]]
        self.ci = len(self.pending)
        return out

    def attach(self, node, leading: list[str]):
        node.comments = leading
        node.trailing = self.take_trailing()
        return node

    def err(self, code: int, repair: str = "") -> CompileError:
        tok = self.t()
        return CompileError(Diag(code, self.file, tok.line, tok.offset, repair))

    def parse(self) -> Module:
        self.eat_kw("module")
        path = self.module_path()
        m = Module(path=path, role=self.role, file=self.file, line=1, source=self.lex.src)
        while self.kw("use"):
            leading = self.take_leading(self.t().line)
            m.uses.append(self.attach(self.parse_use(), leading))
        if self.role == "test":
            while self.kw("test"):
                leading = self.take_leading(self.t().line)
                m.tests.append(self.attach(self.parse_test(), leading))
        else:
            while self.t().kind != "EOF":
                leading = self.take_leading(self.t().line)
                m.items.append(self.attach(self.parse_item(), leading))
        if self.t().kind != "EOF":
            raise self.err(11)
        m.comments = self.rest_comments()
        return m

    def module_path(self) -> str:
        parts = [self.snake()]
        while self.at("."):
            self.bump()
            parts.append(self.snake())
        if len(parts) > 8:
            raise self.err(10)
        return ".".join(parts)

    def snake(self) -> str:
        tok = self.t()
        if not is_name_tok(tok):
            if tok.kind == "KW":
                raise self.err(17)
            raise self.err(11)
        s = tok.val
        self.bump()
        self.check_snake(s, tok)
        return s

    def check_snake(self, s: str, tok: Tok) -> None:
        if not (len(s) >= 2 and s[0].islower() and s.replace("_", "").isalnum()):
            raise CompileError(Diag(16, self.file, tok.line, tok.offset))
        if s.startswith("_") or s.endswith("_") or "__" in s:
            raise CompileError(Diag(16, self.file, tok.line, tok.offset))
        if any(c.isupper() for c in s):
            raise CompileError(Diag(16, self.file, tok.line, tok.offset))

    def pascal(self) -> str:
        tok = self.t()
        if tok.kind != "IDENT":
            raise self.err(11)
        s = tok.val
        self.bump()
        self.check_pascal(s, tok)
        return s

    def check_pascal(self, s: str, tok: Tok) -> None:
        if s in ("T", "K", "V"):
            return
        i = 0
        ok = False
        while i < len(s):
            if not s[i].isupper():
                raise CompileError(Diag(16, self.file, tok.line, tok.offset))
            i += 1
            n = 0
            while i < len(s) and (s[i].islower() or s[i].isdigit()):
                i += 1
                n += 1
            if n < 1:
                raise CompileError(Diag(16, self.file, tok.line, tok.offset))
            ok = True
        if not ok:
            raise CompileError(Diag(16, self.file, tok.line, tok.offset))

    def ident_snake_or_pascal(self) -> str:
        tok = self.t()
        if not is_name_tok(tok):
            raise self.err(11)
        s = tok.val
        if s[0].isupper():
            return self.pascal()
        return self.snake()

    def parse_use(self) -> UseDecl:
        line = self.t().line
        self.eat_kw("use")
        path = self.module_path()
        self.eat("{")
        names = [self.ident_snake_or_pascal()]
        while self.at(","):
            self.bump()
            if self.at("}"):
                raise self.err(11)
            names.append(self.ident_snake_or_pascal())
        self.eat("}")
        return UseDecl(path=path, names=names, line=line)

    def parse_item(self) -> object:
        if self.kw("type"):
            return self.parse_type_decl()
        if self.kw("enum"):
            return self.parse_enum()
        if self.kw("trait"):
            return self.parse_trait()
        if self.kw("provide"):
            return self.parse_provide()
        if self.kw("fn"):
            return self.parse_fn("fn")
        if self.kw("task"):
            return self.parse_fn("task")
        if self.kw("workflow"):
            return self.parse_fn("workflow")
        if self.kw("agent"):
            return self.parse_agent()
        if self.kw("http"):
            return self.parse_http()
        if self.kw("egress"):
            return self.parse_egress()
        raise self.err(11)

    def type_params(self) -> list[str]:
        if not self.at("["):
            return []
        self.bump()
        names = []
        tok = self.t()
        if tok.kind != "IDENT" or tok.val not in ("T", "K", "V"):
            raise self.err(16)
        names.append(self.bump().val)
        while self.at(","):
            self.bump()
            tok = self.t()
            if tok.kind != "IDENT" or tok.val not in ("T", "K", "V"):
                raise self.err(16)
            names.append(self.bump().val)
        self.eat("]")
        return names

    def parse_type_decl(self) -> TypeDecl:
        line = self.t().line
        self.eat_kw("type")
        name = self.pascal()
        tps = self.type_params()
        fields = self.field_block()
        return TypeDecl(name=name, tparams=tps, fields=fields, line=line)

    def field_block(self) -> list[Field]:
        self.eat("{")
        fields = []
        while not self.at("}"):
            if self.at(","):
                raise self.err(38)
            leading = self.take_leading(self.t().line)
            line = self.t().line
            n = self.snake()
            self.eat(":")
            ty = self.parse_type_expr()
            fields.append(self.attach(Field(name=n, ty=ty, line=line), leading))
        self.eat("}")
        return fields

    def parse_enum(self) -> EnumDecl:
        line = self.t().line
        self.eat_kw("enum")
        name = self.pascal()
        self.eat("{")
        vs = []
        while not self.at("}"):
            leading = self.take_leading(self.t().line)
            vn = self.pascal()
            fields: list[Field] = []
            if self.at("{"):
                fields = self.field_block()
            vs.append(self.attach(Variant(name=vn, fields=fields, line=line), leading))
        self.eat("}")
        return EnumDecl(name=name, variants=vs, line=line)

    def parse_trait(self) -> TraitDecl:
        line = self.t().line
        self.eat_kw("trait")
        name = self.pascal()
        tps = self.type_params()
        self.eat("{")
        mem = []
        while not self.at("}"):
            if self.kw("fn"):
                mem.append(self.parse_sig("fn"))
            elif self.kw("task"):
                mem.append(self.parse_sig("task"))
            else:
                raise self.err(11)
        self.eat("}")
        return TraitDecl(name=name, tparams=tps, members=mem, line=line)

    def parse_provide(self) -> ProvideDecl:
        line = self.t().line
        self.eat_kw("provide")
        trait = self.qual_pascal()
        targs = self.maybe_targs()
        self.eat_kw("for")
        if self.t().kind == "KW" and self.t().val in (
            "bool",
            "i32",
            "i64",
            "u32",
            "u64",
            "f64",
            "str",
            "bytes",
            "unit",
        ):
            target = self.bump().val
        else:
            target = self.pascal()
        tgargs = self.maybe_targs()
        members = None
        if self.at("{"):
            self.bump()
            members = []
            while not self.at("}"):
                if self.kw("fn"):
                    members.append(self.parse_fn("fn"))
                elif self.kw("task"):
                    members.append(self.parse_fn("task"))
                else:
                    raise self.err(11)
            self.eat("}")
        return ProvideDecl(
            trait=trait,
            trait_args=targs,
            target=target,
            target_args=tgargs,
            members=members,
            line=line,
        )

    def qual_pascal(self) -> str:
        # module.path.Pascal or Pascal
        parts = []
        if is_name_tok(self.t()) and self.t().val[0].islower():
            parts.append(self.snake())
            while self.at("."):
                # lookahead
                nxt = self.toks[self.i + 1] if self.i + 1 < len(self.toks) else None
                if nxt and nxt.kind == "IDENT" and nxt.val[:1].isupper():
                    self.bump()
                    parts.append(self.pascal())
                    return ".".join(parts)
                self.bump()
                parts.append(self.snake())
            raise self.err(11)
        return self.pascal()

    def maybe_targs(self) -> list[TypeExpr]:
        if not self.at("["):
            return []
        return self.type_args()

    def parse_fn(self, kind: str) -> FnDecl:
        sig = self.parse_sig(kind)
        body = None
        if self.at("{"):
            body = self.parse_block()
        return FnDecl(sig=sig, body=body, line=sig.line)

    def parse_sig(self, kind: str) -> FnSig:
        line = self.t().line
        self.eat_kw(kind)
        name = self.snake()
        tps = self.type_params()
        params = self.param_list()
        self.eat("ARROW")
        ret = self.parse_type_expr()
        effects: list[str] = []
        if kind in ("task", "workflow"):
            effects = self.effects_clause()
        contracts = []
        while self.kw("requires") or self.kw("ensures"):
            contracts.append(self.parse_contract())
        return FnSig(
            kind=kind,
            name=name,
            tparams=tps,
            params=params,
            ret=ret,
            effects=effects,
            contracts=contracts,
            line=line,
        )

    def param_list(self) -> list[Param]:
        self.eat("(")
        if self.at(")"):
            self.bump()
            return []
        ps = [self.param()]
        while self.at(","):
            self.bump()
            if self.at(")"):
                raise self.err(11)
            ps.append(self.param())
        self.eat(")")
        return ps

    def param(self) -> Param:
        line = self.t().line
        n = self.snake()
        self.eat(":")
        ty = self.parse_type_expr()
        return Param(name=n, ty=ty, line=line)

    def effects_clause(self) -> list[str]:
        self.eat_kw("effects")
        self.eat("{")
        if self.at("}"):
            self.bump()
            return []
        xs = [self.effect()]
        while self.at(","):
            self.bump()
            if self.at("}"):
                raise self.err(11)
            xs.append(self.effect())
        self.eat("}")
        return xs

    def effect(self) -> str:
        # filesystem.read as IDENT? filesystem is ident, . read is ident
        tok = self.t()
        if tok.kind == "KW" and tok.val in (
            "network",
            "time",
            "random",
            "log",
            "model",
            "secret",
            "observe",
        ):
            return self.bump().val
        if tok.kind == "IDENT" and tok.val in ("network", "time", "random", "log", "model", "ffi", "secret", "observe", "resource"):
            return self.bump().val
        if tok.kind == "IDENT" and tok.val in ("filesystem", "database"):
            a = self.bump().val
            self.eat(".")
            b = self.snake()
            name = f"{a}.{b}"
            if name not in EFFECTS:
                raise CompileError(Diag(52, self.file, tok.line, tok.offset))
            return name
        if tok.kind == "KW" and tok.val == "http":
            raise CompileError(Diag(52, self.file, tok.line, tok.offset))
        raise CompileError(Diag(52, self.file, tok.line, tok.offset))

    def parse_contract(self) -> Contract:
        line = self.t().line
        kind = self.bump().val
        if self.kw("open"):
            self.bump()
            oid = self.snake()
            return Contract(kind=kind, open_id=oid, line=line)
        expr = self.parse_expr()
        return Contract(kind=kind, expr=expr, line=line)

    def parse_agent(self) -> AgentDecl:
        line = self.t().line
        self.eat_kw("agent")
        name = self.pascal()
        effects = self.effects_clause()
        self.eat_kw("tasks")
        self.eat("{")
        tasks = []
        if not self.at("}"):
            tasks.append(self.snake())
            while self.at(","):
                self.bump()
                if self.at("}"):
                    raise self.err(11)
                tasks.append(self.snake())
        self.eat("}")
        evo = (None, None, None)
        if self.kw("evolve"):
            self.bump()
            self.eat("{")
            self.eat_kw("max")
            mx = int(self.eat("INT").val)
            self.eat_kw("timeout_ms")
            tm = int(self.eat("INT").val)
            self.eat_kw("reservoir")
            rs = int(self.eat("INT").val)
            self.eat("}")
            evo = (mx, tm, rs)
        return AgentDecl(
            name=name,
            effects=effects,
            tasks=tasks,
            evolve_max=evo[0],
            evolve_timeout=evo[1],
            evolve_reservoir=evo[2],
            line=line,
        )

    def parse_http(self) -> HttpDecl:
        line = self.t().line
        self.eat_kw("http")
        self.eat("{")
        routes = []
        while not self.at("}"):
            if not (self.t().kind == "KW" and self.t().val in ("get", "post", "put", "patch", "delete")):
                raise self.err(90)
            verb = self.bump().val
            path = self.eat("STRING").val
            handler = self.snake()
            routes.append(HttpRoute(verb=verb, path=path, handler=handler, line=line))
        self.eat("}")
        return HttpDecl(routes=routes, line=line)

    def parse_egress(self) -> EgressDecl:
        line = self.t().line
        self.eat_kw("egress")
        self.eat("{")
        origins = []
        if not self.at("}"):
            origins.append(self.eat("STRING").val)
            while self.at(","):
                self.bump()
                if self.at("}"):
                    raise self.err(11)
                origins.append(self.eat("STRING").val)
        self.eat("}")
        return EgressDecl(origins=origins, line=line)

    def parse_test(self) -> TestDecl:
        line = self.t().line
        self.eat_kw("test")
        name = self.snake()
        body = self.parse_block()
        return TestDecl(name=name, body=body, line=line)

    def parse_block(self) -> Block:
        line = self.t().line
        self.eat("{")
        stmts = []
        while not self.at("}"):
            leading = self.take_leading(self.t().line)
            stmts.append(self.attach(self.parse_stmt(), leading))
        self.eat("}")
        block = Block(stmts=stmts, line=line)
        block.comments = self.take_leading(self.toks[self.i - 1].line)
        return block

    def parse_stmt(self) -> object:
        if self.kw("return"):
            line = self.t().line
            self.bump()
            e = self.parse_expr()
            return ReturnStmt(expr=e, line=line)
        if self.kw("if"):
            return self.parse_if()
        if self.kw("for"):
            return self.parse_for()
        if self.kw("unresolved"):
            line = self.t().line
            self.bump()
            n = self.snake()
            return UnresolvedStmt(name=n, line=line)
        # bind or expr
        if is_name_tok(self.t()) and self.t().val[0].islower():
            nxt = self.toks[self.i + 1] if self.i + 1 < len(self.toks) else None
            if nxt and nxt.kind == "=":
                line = self.t().line
                n = self.snake()
                self.eat("=")
                if self.kw("if"):
                    raise CompileError(Diag(11, self.file, self.t().line, self.t().offset))
                e = self.parse_expr()
                return BindStmt(name=n, expr=e, line=line)
        if self.kw("if"):
            raise CompileError(Diag(71, self.file, self.t().line, self.t().offset))
        e = self.parse_expr()
        return ExprStmt(expr=e, line=getattr(e, "line", 1))

    def parse_if(self) -> IfStmt:
        line = self.t().line
        self.eat_kw("if")
        cond = self.parse_expr()
        then = self.parse_block()
        els = None
        if self.kw("else"):
            self.bump()
            els = self.parse_block()
        return IfStmt(cond=cond, then=then, els=els, line=line)

    def parse_for(self) -> ForStmt:
        line = self.t().line
        self.eat_kw("for")
        n = self.snake()
        self.eat_kw("in")
        it = self.parse_expr()
        body = self.parse_block()
        return ForStmt(name=n, iter=it, body=body, line=line)

    def parse_type_expr(self) -> TypeExpr:
        line = self.t().line
        parts = [self.parse_named_type()]
        while self.at("|"):
            self.bump()
            parts.append(self.parse_named_type())
        return TypeExpr(parts=parts, line=line)

    def parse_named_type(self) -> NamedType:
        line = self.t().line
        tok = self.t()
        opt = False
        if tok.kind == "IDENT" and tok.val in ("int", "string", "any", "dict"):
            # E020 has no exact replacement text (docs/diagnostics.md), so it
            # carries no repair bytes: a repair loop must stop and think here.
            raise CompileError(Diag(20, self.file, tok.line, tok.offset))
        if tok.kind == "KW" and tok.val in (
            "bool",
            "i32",
            "i64",
            "u32",
            "u64",
            "f64",
            "str",
            "bytes",
            "unit",
            "list",
            "map",
            "Self",
        ):
            name = self.bump().val
            args = []
            if name in ("list", "map"):
                args = self.type_args()
            if self.at("?"):
                self.bump()
                opt = True
            return NamedType(name=name, args=args, optional=opt, line=line)
        if tok.kind == "IDENT" and tok.val in ("T", "K", "V"):
            name = self.bump().val
            if self.at("?"):
                self.bump()
                opt = True
            return NamedType(name=name, optional=opt, line=line)
        if tok.kind == "IDENT" and tok.val[0].islower():
            name = self.qual_pascal()
        elif tok.kind == "IDENT":
            name = self.pascal()
        else:
            if tok.val == "int" or tok.val == "string":
                raise CompileError(Diag(20, self.file, tok.line, tok.offset))
            raise self.err(20)
        args = self.maybe_targs()
        if self.at("?"):
            self.bump()
            opt = True
        return NamedType(name=name, args=args, optional=opt, line=line)

    def type_args(self) -> list[TypeExpr]:
        self.eat("[")
        xs = [self.parse_type_expr()]
        while self.at(","):
            self.bump()
            if self.at("]"):
                raise self.err(11)
            xs.append(self.parse_type_expr())
        self.eat("]")
        return xs

    def parse_expr(self) -> object:
        return self.parse_or()

    def parse_or(self) -> object:
        e = self.parse_and()
        while self.kw("or"):
            line = self.t().line
            self.bump()
            e = BinExpr(op="or", left=e, right=self.parse_and(), line=line)
        return e

    def parse_and(self) -> object:
        e = self.parse_not()
        while self.kw("and"):
            line = self.t().line
            self.bump()
            e = BinExpr(op="and", left=e, right=self.parse_not(), line=line)
        return e

    def parse_not(self) -> object:
        if self.kw("not"):
            line = self.t().line
            self.bump()
            return UnaryExpr(op="not", inner=self.parse_not(), line=line)
        return self.parse_cmp()

    def parse_cmp(self) -> object:
        e = self.parse_add()
        tok = self.t()
        if tok.kind in ("EQEQ", "NE", "LE", "GE") or tok.val in ("<", ">"):
            op = tok.val
            line = tok.line
            self.bump()
            e = BinExpr(op=op, left=e, right=self.parse_add(), line=line)
        return e

    def parse_add(self) -> object:
        e = self.parse_mul()
        while self.at("+") or (self.at("-") and True):
            if self.t().val not in ("+", "-"):
                break
            op = self.t().val
            line = self.t().line
            self.bump()
            e = BinExpr(op=op, left=e, right=self.parse_mul(), line=line)
        return e

    def parse_mul(self) -> object:
        e = self.parse_unary()
        while self.t().val in ("*", "/", "%"):
            op = self.t().val
            line = self.t().line
            self.bump()
            e = BinExpr(op=op, left=e, right=self.parse_unary(), line=line)
        return e

    def parse_unary(self) -> object:
        if self.at("-"):
            line = self.t().line
            self.bump()
            return UnaryExpr(op="-", inner=self.parse_unary(), line=line)
        return self.parse_prefix()

    def parse_prefix(self) -> object:
        e = self.parse_primary()
        while True:
            if self.at("."):
                # field or continue name already in primary
                line = self.t().line
                self.bump()
                if not is_name_tok(self.t()):
                    raise self.err(11)
                if self.t().val[0].isupper():
                    # enum-like, shouldn't happen after expr
                    raise self.err(37)
                n = self.snake()
                e = FieldExpr(base=e, name=n, line=line)
                continue
            if self.at("[") or self.at("("):
                targs = []
                if self.at("["):
                    targs = self.type_args()
                if not self.at("("):
                    raise self.err(11)
                line = self.t().line
                self.bump()
                args = []
                if not self.at(")"):
                    args.append(self.parse_expr())
                    while self.at(","):
                        self.bump()
                        if self.at(")"):
                            raise self.err(11)
                        args.append(self.parse_expr())
                self.eat(")")
                e = CallExpr(callee=e, targs=targs, args=args, line=line)
                continue
            break
        return e

    # Not a language limit: the host stack is finite, and S27 says an
    # implementation that cannot proceed reports E001 rather than crashing.
    MAX_EXPR_DEPTH = 64

    def parse_primary(self) -> object:
        self.depth += 1
        try:
            if self.depth > self.MAX_EXPR_DEPTH:
                raise CompileError(Diag(1, self.file, self.t().line, self.t().offset))
            return self._parse_primary()
        finally:
            self.depth -= 1

    def _parse_primary(self) -> object:
        tok = self.t()
        line = tok.line
        if self.kw("if"):
            # C025: `if` is a statement; in a value position that is E071.
            raise CompileError(Diag(71, self.file, line, tok.offset))
        if tok.kind == "INT":
            self.bump()
            return IntLit(value=tok.val, line=line)
        if tok.kind == "FLOAT":
            self.bump()
            return FloatLit(value=tok.val, line=line)
        if tok.kind == "STRING":
            self.bump()
            return StrLit(raw=tok.val, line=line)
        if self.kw("true") or self.kw("false"):
            self.bump()
            return BoolLit(value=tok.val == "true", line=line)
        if self.kw("none"):
            self.bump()
            return NoneLit(line=line)
        if self.kw("unit"):
            self.bump()
            return UnitLit(line=line)
        if self.kw("some"):
            self.bump()
            self.eat("(")
            inner = self.parse_expr()
            self.eat(")")
            return SomeExpr(inner=inner, line=line)
        if self.kw("match"):
            return self.parse_match()
        if self.kw("parallel"):
            return self.parse_parallel()
        if self.at("("):
            self.bump()
            e = self.parse_expr()
            self.eat(")")
            return e
        if is_name_tok(tok):
            return self.parse_name_or_construct()
        raise self.err(11)

    def parse_name_or_construct(self) -> object:
        line = self.t().line
        parts: list[str] = []
        if self.t().val[0].isupper():
            parts.append(self.pascal())
        else:
            parts.append(self.snake())
        while self.at("."):
            nxt = self.toks[self.i + 1]
            if not is_name_tok(nxt):
                break
            self.bump()
            if nxt.val[0].isupper():
                parts.append(self.pascal())
            else:
                parts.append(self.snake())
        if self.at("{") and parts[-1][0].isupper():
            fields = self.parse_field_lits()
            is_enum = len(parts) >= 2 and parts[-1][0].isupper() and parts[-2][0].isupper()
            return ConstructExpr(ty=".".join(parts), fields=fields, enum=is_enum, line=line)
        return NameExpr(parts=parts, line=line)

    def parse_field_lits(self) -> list[tuple[str, object]]:
        self.eat("{")
        fs = []
        while not self.at("}"):
            if self.at(","):
                raise self.err(38)
            n = self.snake()
            self.eat(":")
            e = self.parse_expr()
            fs.append((n, e))
        self.eat("}")
        return fs

    def parse_match(self) -> MatchExpr:
        line = self.t().line
        self.eat_kw("match")
        scrut = self.parse_expr()
        self.eat("{")
        arms = []
        while not self.at("}"):
            leading = self.take_leading(self.t().line)
            pat = self.parse_pattern()
            self.eat("ARROW")
            body = self.parse_expr()
            arms.append(self.attach(MatchArm(pattern=pat, body=body, line=line), leading))
        self.eat("}")
        return MatchExpr(scrut=scrut, arms=arms, line=line)

    def parse_pattern(self) -> object:
        line = self.t().line
        if self.kw("none"):
            self.bump()
            return PatNone(line=line)
        if self.kw("some"):
            self.bump()
            self.eat("(")
            n = self.snake()
            self.eat(")")
            return PatSome(name=n, line=line)
        parts: list[str] = []
        if self.t().kind == "IDENT" and self.t().val[:1].isupper():
            parts.append(self.pascal())
        else:
            parts.append(self.snake())
        while self.at("."):
            nxt = self.toks[self.i + 1]
            if not is_name_tok(nxt):
                raise self.err(11)
            self.bump()
            if nxt.val[:1].isupper():
                parts.append(self.pascal())
            else:
                parts.append(self.snake())
        binds: list[str] = []
        if self.at("{"):
            self.bump()
            while not self.at("}"):
                if self.at(","):
                    raise self.err(38)
                binds.append(self.snake())
            self.eat("}")
        return PatType(name=".".join(parts), binds=binds, line=line)

    def parse_parallel(self) -> ParallelExpr:
        line = self.t().line
        self.eat_kw("parallel")
        if not self.kw("max"):
            raise CompileError(Diag(54, self.file, line, self.t().offset))
        self.bump()
        mx = int(self.eat("INT").val)
        self.eat_kw("timeout_ms")
        tm = int(self.eat("INT").val)
        self.eat("{")
        arms = []
        while not self.at("}"):
            arms.append(self.parse_expr())
        self.eat("}")
        if mx <= 0 or tm <= 0:
            raise CompileError(Diag(54, self.file, line, 0))
        return ParallelExpr(max_n=mx, timeout_ms=tm, arms=arms, line=line)


def parse_module(src: str, file: str, role: str) -> Module:
    return Parser(src, file, role).parse()
