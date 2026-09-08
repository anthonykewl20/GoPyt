from __future__ import annotations

from dataclasses import dataclass

from gopyt.diag import CompileError, Diag

KEYWORDS = {
    "module",
    "use",
    "type",
    "enum",
    "fn",
    "task",
    "trait",
    "agent",
    "workflow",
    "match",
    "if",
    "else",
    "for",
    "in",
    "parallel",
    "return",
    "effects",
    "requires",
    "ensures",
    "open",
    "some",
    "none",
    "true",
    "false",
    "test",
    "http",
    "tasks",
    "provide",
    "egress",
    "evolve",
    "unresolved",
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
    "Self",
    "and",
    "or",
    "not",
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "max",
    "timeout_ms",
    "reservoir",
}

DYNAMIC = {"eval", "exec", "system", "popen", "unsafe", "shell"}
ILLEGAL_KW = {
    "class",
    "struct",
    "record",
    "object",
    "interface",
    "abstract",
    "async",
    "await",
    "go",
    "throw",
    "try",
    "catch",
    "except",
    "null",
    "nil",
    "var",
    "let",
    "const",
    "public",
    "private",
    "while",
    "import",
    "from",
} | DYNAMIC

TWO = {
    "==": "EQEQ",
    "!=": "NE",
    "<=": "LE",
    ">=": "GE",
    "->": "ARROW",
}


@dataclass
class Tok:
    kind: str
    val: str
    line: int
    col: int
    offset: int


class Lexer:
    def __init__(self, src: str, file: str) -> None:
        if "\t" in src:
            line = src.split("\t")[0].count("\n") + 1
            raise CompileError(Diag(2, file, line, src.index("\t")))
        if "\r" in src:
            raise CompileError(Diag(3, file, 1, 0))
        if len(src.encode("utf-8")) > 1_048_576:
            raise CompileError(Diag(10, file, 1, 0))
        self.src = src
        self.file = file
        self.i = 0
        self.line = 1
        self.col = 1
        self.comments: list[tuple[int, str, bool]] = []

    def peek(self) -> str:
        return self.src[self.i] if self.i < len(self.src) else ""

    def bump(self) -> str:
        ch = self.peek()
        self.i += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def skip_ws(self) -> None:
        while True:
            ch = self.peek()
            if ch == "" :
                break
            if ch in " \n":
                self.bump()
                continue
            if ch == "/" and self.i + 1 < len(self.src) and self.src[self.i + 1] == "/":
                start_line = self.line
                head = self.src.rfind("\n", 0, self.i) + 1
                own_line = self.src[head : self.i].strip() == ""
                self.bump()
                self.bump()
                buf = []
                while self.peek() and self.peek() != "\n":
                    buf.append(self.bump())
                # Text after `//`, trailing whitespace removed (docs/fmt.md).
                self.comments.append((start_line, "".join(buf).rstrip(), own_line))
                continue
            break

    def ident_or_kw(self) -> Tok:
        line, col, off = self.line, self.col, self.i
        buf = [self.bump()]
        while self.peek().isalnum() or self.peek() == "_":
            buf.append(self.bump())
        s = "".join(buf)
        if not s.isascii():
            raise CompileError(Diag(16, self.file, line, off))
        if len(s.encode("utf-8")) > 64:
            raise CompileError(Diag(10, self.file, line, off))
        if s in ILLEGAL_KW:
            code = 110 if s in DYNAMIC else 17
            raise CompileError(Diag(code, self.file, line, off))
        if s in KEYWORDS:
            return Tok("KW", s, line, col, off)
        if s == "_":
            raise CompileError(Diag(39, self.file, line, off))
        if self.peek() == '"':
            # S8: string literals are `"..."` only. There is no prefix form, so
            # f"..." / b"..." is an unexpected character, not an identifier.
            raise CompileError(Diag(10, self.file, line, self.i))
        return Tok("IDENT", s, line, col, off)

    def number(self) -> Tok:
        line, col, off = self.line, self.col, self.i
        if self.peek() == "0":
            self.bump()
            if self.peek().isdigit():
                raise CompileError(Diag(10, self.file, line, off, repair="0"))
            if self.peek() == ".":
                buf = ["0", self.bump()]
                if not self.peek().isdigit():
                    raise CompileError(Diag(10, self.file, line, off))
                while self.peek().isdigit():
                    buf.append(self.bump())
                self._reject_number_suffix(line)
                if not "".join(buf).isascii():
                    raise CompileError(Diag(10, self.file, line, off))
                return Tok("FLOAT", "".join(buf), line, col, off)
            self._reject_number_suffix(line)
            return Tok("INT", "0", line, col, off)
        buf = []
        while self.peek().isdigit():
            buf.append(self.bump())
        if self.peek() == ".":
            buf.append(self.bump())
            if not self.peek().isdigit():
                raise CompileError(Diag(10, self.file, line, off))
            while self.peek().isdigit():
                buf.append(self.bump())
            self._reject_number_suffix(line)
            if not "".join(buf).isascii():
                raise CompileError(Diag(10, self.file, line, off))
            return Tok("FLOAT", "".join(buf), line, col, off)
        n = "".join(buf)
        if not n.isascii() or len(n) > 19 or int(n) > 2**63 - 1:
            raise CompileError(Diag(10, self.file, line, off))
        self._reject_number_suffix(line)
        return Tok("INT", n, line, col, off)

    def _reject_number_suffix(self, line: int) -> None:
        """implementer.md 6: no hex integers, no underscores, no suffixes."""
        if self.peek().isalnum() or self.peek() == "_":
            raise CompileError(Diag(10, self.file, line, self.i))

    def string(self) -> Tok:
        line, col, off = self.line, self.col, self.i
        self.bump()
        buf = ['"']
        while True:
            ch = self.peek()
            if ch == "":
                raise CompileError(Diag(10, self.file, line, off))
            if ord(ch) < 32 and ch not in "\t":
                raise CompileError(Diag(10, self.file, line, off))
            if ch == "\\":
                buf.append(self.bump())
                nxt = self.peek()
                if nxt not in '\\"ntr':
                    raise CompileError(Diag(10, self.file, line, off))
                buf.append(self.bump())
                continue
            if ch == '"':
                buf.append(self.bump())
                break
            buf.append(self.bump())
        return Tok("STRING", "".join(buf), line, col, off)

    def next(self) -> Tok:
        self.skip_ws()
        ch = self.peek()
        line, col, off = self.line, self.col, self.i
        if ch == "":
            return Tok("EOF", "", line, col, off)
        if ch.isalpha() or ch == "_":
            return self.ident_or_kw()
        if ch.isdigit():
            return self.number()
        if ch == '"':
            return self.string()
        two = self.src[self.i : self.i + 2]
        if two in TWO:
            self.bump()
            self.bump()
            return Tok(TWO[two], two, line, col, off)
        if two == "=>":
            raise CompileError(Diag(11, self.file, line, off))
        if two == "&&":
            raise CompileError(Diag(11, self.file, line, off))
        self.bump()
        return Tok(ch, ch, line, col, off)

    def tokenize(self) -> list[Tok]:
        out = []
        while True:
            t = self.next()
            out.append(t)
            if t.kind == "EOF":
                break
        return out
