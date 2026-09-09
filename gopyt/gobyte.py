"""build/out.gobyte: encode, decode, and validate (docs/bytecode.md).

Malformed bytecode is never partially executed: decode() rejects with
GOPYT_E100 before the VM sees a single instruction.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from gopyt import ops
from gopyt.toolchain import FINGERPRINT
from gopyt.diag import CompileError, Diag

MAGIC = b"GPYT"
VERSION = 3
FLAGS = 0

TAG_I64 = 1
TAG_F64 = 2
TAG_STR = 3
TAG_BYTES = 4
TAG_BOOL = 5
TAG_UNIT = 6
TAG_NONE = 7
TAG_TYPE = 8
TAG_FN = 9
TAG_EFFECT = 10

TE_BOOL, TE_I32, TE_I64, TE_U32, TE_U64, TE_F64, TE_STR, TE_BYTES, TE_UNIT = range(1, 10)
TE_OPT = 10
TE_LIST = 11
TE_MAP = 12
TE_NOM = 13
TE_UNION = 14

PRIM_TAG = {
    "bool": TE_BOOL,
    "i32": TE_I32,
    "i64": TE_I64,
    "u32": TE_U32,
    "u64": TE_U64,
    "f64": TE_F64,
    "str": TE_STR,
    "bytes": TE_BYTES,
    "unit": TE_UNIT,
}


def e100() -> CompileError:
    return CompileError(Diag(100, None, None, 0))


@dataclass
class Const:
    tag: int
    value: object = None


@dataclass
class TExpr:
    tag: int
    a: int = 0
    b: int = 0
    members: tuple[int, ...] = ()


@dataclass
class TypeDef:
    kind: int  # 1 record, 2 enum, 3 opaque
    name: int
    fields: list[tuple[int, int]] = field(default_factory=list)
    variants: list[tuple[int, list[tuple[int, int]]]] = field(default_factory=list)


@dataclass
class Func:
    name: int
    kind: int
    arity: int
    nlocals: int
    effects: int
    params: list[int] = field(default_factory=list)
    ret: int = 0
    locals: list[int] = field(default_factory=list)
    code: bytes = b""
    # Load-time analysis, not part of the file:
    boundaries: frozenset[int] = frozenset()


@dataclass
class Route:
    module: int
    method: int
    path: int
    handler: int
    params: list[int] = field(default_factory=list)  # const indices, decl order


@dataclass
class EgressEntry:
    module: int
    origin: int


@dataclass
class EvolveEntry:
    module: int
    max_candidates: int
    timeout_ms: int
    reservoir: int


@dataclass
class Artifact:
    consts: list[Const] = field(default_factory=list)
    texprs: list[TExpr] = field(default_factory=list)
    types: list[TypeDef] = field(default_factory=list)
    funcs: list[Func] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    egress: list[EgressEntry] = field(default_factory=list)
    evolve: list[EvolveEntry] = field(default_factory=list)

    toolchain: bytes = FINGERPRINT

    def const_str(self, index: int) -> str:
        c = self.consts[index]
        if c.tag != TAG_STR:
            raise e100()
        return c.value


# ---------------------------------------------------------------- encoding


class Writer:
    def __init__(self) -> None:
        self.buf = bytearray()

    def u8(self, v: int) -> None:
        self.buf += struct.pack("<B", v)

    def u16(self, v: int) -> None:
        self.buf += struct.pack("<H", v)

    def u32(self, v: int) -> None:
        self.buf += struct.pack("<I", v)

    def i32(self, v: int) -> None:
        self.buf += struct.pack("<i", v)

    def i64(self, v: int) -> None:
        self.buf += struct.pack("<q", v)

    def f64(self, v: float) -> None:
        self.buf += struct.pack("<d", v)

    def blob(self, data: bytes) -> None:
        self.u32(len(data))
        self.buf += data


def encode(art: Artifact) -> bytes:
    if art.toolchain != FINGERPRINT:
        raise e100()
    w = Writer()
    w.buf += MAGIC
    w.u8(VERSION)
    w.u8(FLAGS)
    w.buf += art.toolchain
    w.u32(len(art.consts))
    for c in art.consts:
        w.u8(c.tag)
        if c.tag == TAG_I64:
            w.i64(c.value)
        elif c.tag == TAG_F64:
            w.f64(c.value)
        elif c.tag == TAG_STR:
            w.blob(c.value.encode("utf-8"))
        elif c.tag == TAG_BYTES:
            w.blob(c.value)
        elif c.tag == TAG_BOOL:
            w.u8(1 if c.value else 0)
        elif c.tag in (TAG_UNIT, TAG_NONE):
            pass
        elif c.tag in (TAG_TYPE, TAG_FN):
            w.u32(c.value)
        elif c.tag == TAG_EFFECT:
            w.u16(c.value)
        else:
            raise e100()
    w.u32(len(art.texprs))
    for t in art.texprs:
        w.u8(t.tag)
        if t.tag in (TE_OPT, TE_LIST):
            w.u32(t.a)
        elif t.tag == TE_MAP:
            w.u32(t.a)
            w.u32(t.b)
        elif t.tag == TE_NOM:
            w.u32(t.a)
        elif t.tag == TE_UNION:
            w.u16(len(t.members))
            for mem in t.members:
                w.u32(mem)
    w.u32(len(art.types))
    for ty in art.types:
        w.u8(ty.kind)
        w.u32(ty.name)
        if ty.kind == 1:
            w.u16(len(ty.fields))
            for fname, fty in ty.fields:
                w.u32(fname)
                w.u32(fty)
        elif ty.kind == 2:
            w.u16(len(ty.variants))
            for vname, fields in ty.variants:
                w.u32(vname)
                w.u16(len(fields))
                for fname, fty in fields:
                    w.u32(fname)
                    w.u32(fty)
        else:
            w.u16(0)
    w.u32(len(art.funcs))
    for f in art.funcs:
        w.u32(f.name)
        w.u8(f.kind)
        w.u16(f.arity)
        w.u16(f.nlocals)
        w.u16(f.effects)
        for p in f.params:
            w.u32(p)
        w.u32(f.ret)
        for lt in f.locals:
            w.u32(lt)
        if f.kind == ops.KIND_NATIVE:
            w.u32(0)
        else:
            w.blob(f.code)
    w.u32(len(art.routes))
    for r in art.routes:
        w.u32(r.module)
        w.u8(r.method)
        w.u32(r.path)
        w.u32(r.handler)
        w.u16(len(r.params))
        for name in r.params:
            w.u32(name)
    w.u32(len(art.egress))
    for eg in art.egress:
        w.u32(eg.module)
        w.u32(eg.origin)
    w.u32(len(art.evolve))
    for entry in art.evolve:
        w.u32(entry.module)
        w.u32(entry.max_candidates)
        w.u32(entry.timeout_ms)
        w.u32(entry.reservoir)
    return bytes(w.buf)


# ---------------------------------------------------------------- decoding


class Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.i = 0

    def take(self, n: int) -> bytes:
        if self.i + n > len(self.data):
            raise e100()
        out = self.data[self.i : self.i + n]
        self.i += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self.take(8))[0]

    def f64(self) -> float:
        return struct.unpack("<d", self.take(8))[0]

    def blob(self) -> bytes:
        return self.take(self.u32())


def decode(data: bytes) -> Artifact:
    r = Reader(data)
    if r.take(4) != MAGIC or r.u8() != VERSION or r.u8() != FLAGS:
        raise e100()
    fingerprint = r.take(32)
    if fingerprint != FINGERPRINT:
        raise e100()
    art = Artifact(toolchain=fingerprint)
    for _ in range(r.u32()):
        tag = r.u8()
        if tag == TAG_I64:
            art.consts.append(Const(tag, r.i64()))
        elif tag == TAG_F64:
            art.consts.append(Const(tag, r.f64()))
        elif tag == TAG_STR:
            raw = r.blob()
            try:
                art.consts.append(Const(tag, raw.decode("utf-8")))
            except UnicodeDecodeError:
                raise e100()
        elif tag == TAG_BYTES:
            art.consts.append(Const(tag, r.blob()))
        elif tag == TAG_BOOL:
            v = r.u8()
            if v > 1:
                raise e100()
            art.consts.append(Const(tag, v == 1))
        elif tag in (TAG_UNIT, TAG_NONE):
            art.consts.append(Const(tag, None))
        elif tag in (TAG_TYPE, TAG_FN):
            art.consts.append(Const(tag, r.u32()))
        elif tag == TAG_EFFECT:
            art.consts.append(Const(tag, r.u16()))
        else:
            raise e100()
    for _ in range(r.u32()):
        tag = r.u8()
        if tag in range(1, 10):
            art.texprs.append(TExpr(tag))
        elif tag in (TE_OPT, TE_LIST, TE_NOM):
            art.texprs.append(TExpr(tag, r.u32()))
        elif tag == TE_MAP:
            art.texprs.append(TExpr(tag, r.u32(), r.u32()))
        elif tag == TE_UNION:
            n = r.u16()
            if n < 2:
                raise e100()
            art.texprs.append(TExpr(tag, members=tuple(r.u32() for _ in range(n))))
        else:
            raise e100()
    for _ in range(r.u32()):
        kind = r.u8()
        if kind not in (1, 2, 3):
            raise e100()
        name = r.u32()
        count = r.u16()
        td = TypeDef(kind, name)
        if kind == 1:
            td.fields = [(r.u32(), r.u32()) for _ in range(count)]
        elif kind == 2:
            for _v in range(count):
                vname = r.u32()
                nf = r.u16()
                td.variants.append((vname, [(r.u32(), r.u32()) for _ in range(nf)]))
        elif count != 0:
            raise e100()
        art.types.append(td)
    for _ in range(r.u32()):
        name = r.u32()
        kind = r.u8()
        if kind not in (1, 2, 3, 4, 5, 6):
            raise e100()
        arity = r.u16()
        nlocals = r.u16()
        effects = r.u16()
        params = [r.u32() for _ in range(arity)]
        ret = r.u32()
        if nlocals < arity:
            raise e100()
        locs = [r.u32() for _ in range(nlocals - arity)]
        if kind == ops.KIND_NATIVE:
            if r.u32() != 0:
                raise e100()
            code = b""
        else:
            code = r.blob()
        art.funcs.append(Func(name, kind, arity, nlocals, effects, params, ret, locs, code))
    for _ in range(r.u32()):
        module, method, path, handler = r.u32(), r.u8(), r.u32(), r.u32()
        params = [r.u32() for _ in range(r.u16())]
        art.routes.append(Route(module, method, path, handler, params))
    for _ in range(r.u32()):
        art.egress.append(EgressEntry(r.u32(), r.u32()))
    for _ in range(r.u32()):
        art.evolve.append(EvolveEntry(r.u32(), r.u32(), r.u32(), r.u32()))
    if r.i != len(data):
        raise e100()
    try:
        validate(art)
    except RecursionError:
        raise e100()
    return art


# ---------------------------------------------------------------- validation


def validate(art: Artifact) -> None:
    if art.toolchain != FINGERPRINT:
        raise e100()
    nconst = len(art.consts)
    ntexpr = len(art.texprs)
    ntype = len(art.types)
    nfn = len(art.funcs)
    for c in art.consts:
        if c.tag == TAG_TYPE and c.value >= ntype:
            raise e100()
        if c.tag == TAG_FN and c.value >= nfn:
            raise e100()
        if c.tag == TAG_EFFECT and c.value >= (1 << 12):
            raise e100()
    for i, t in enumerate(art.texprs):
        refs = []
        if t.tag in (TE_OPT, TE_LIST):
            refs = [t.a]
        elif t.tag == TE_MAP:
            refs = [t.a, t.b]
        elif t.tag == TE_UNION:
            refs = list(t.members)
        elif t.tag == TE_NOM:
            if t.a >= ntype:
                raise e100()
        for ref in refs:
            if ref >= ntexpr:
                raise e100()
    # TypeExpr edges must be acyclic; recursive records go through Type ids.
    done = set()
    active = set()
    for start in range(ntexpr):
        work = [(start, False)]
        while work:
            index, leaving = work.pop()
            if leaving:
                active.remove(index)
                done.add(index)
                continue
            if index in done:
                continue
            if index in active:
                raise e100()
            active.add(index)
            work.append((index, True))
            t = art.texprs[index]
            refs = ([t.a] if t.tag in (TE_OPT, TE_LIST) else
                    [t.a, t.b] if t.tag == TE_MAP else
                    list(t.members) if t.tag == TE_UNION else [])
            work.extend((ref, False) for ref in refs)
    for t in art.texprs:
        if t.tag == TE_MAP and art.texprs[t.a].tag not in (TE_STR, TE_I64, TE_BOOL):
            raise e100()
        if t.tag == TE_UNION:
            identities = {(art.texprs[m].tag, art.texprs[m].a if art.texprs[m].tag == TE_NOM else 0)
                          for m in t.members}
            if len(identities) != len(t.members) or any(
                art.texprs[m].tag not in (TE_NOM, TE_BOOL, TE_I32, TE_I64, TE_U32,
                                         TE_U64, TE_STR, TE_BYTES, TE_UNIT)
                for m in t.members
            ):
                raise e100()
    type_names = set()
    for td in art.types:
        if td.name >= nconst or art.consts[td.name].tag != TAG_STR:
            raise e100()
        name = art.const_str(td.name)
        if name in type_names or (td.kind == 3 and name != "core.secret.Secret"):
            raise e100()
        if name == "core.secret.Secret" and td.kind != 3:
            raise e100()
        type_names.add(name)
        for fname, fty in td.fields:
            if fname >= nconst or fty >= ntexpr or art.consts[fname].tag != TAG_STR:
                raise e100()
        for vname, fields in td.variants:
            if vname >= nconst or art.consts[vname].tag != TAG_STR:
                raise e100()
            for fname, fty in fields:
                if fname >= nconst or fty >= ntexpr or art.consts[fname].tag != TAG_STR:
                    raise e100()
        field_groups = [td.fields] + [fields for _name, fields in td.variants]
        for fields in field_groups:
            names = [art.const_str(name) for name, _ty in fields]
            if len(set(names)) != len(names):
                raise e100()
        names = [art.const_str(name) for name, _fields in td.variants]
        if len(set(names)) != len(names):
            raise e100()
    for f in art.funcs:
        if f.name >= nconst or art.consts[f.name].tag != TAG_STR:
            raise e100()
        if f.effects >= (1 << 12):
            raise e100()
        if f.kind == ops.KIND_FN and f.effects != 0:
            raise e100()
        if f.kind != ops.KIND_NATIVE and f.effects & ops.FFI_BIT:
            raise e100()
        for te in f.params + f.locals + [f.ret]:
            if te >= ntexpr:
                raise e100()
    # Validate all callable metadata before following a call to a later entry.
    for f in art.funcs:
        if f.kind != ops.KIND_NATIVE:
            _validate_code(art, f)
    for r in art.routes:
        if r.module >= nconst or r.path >= nconst or r.handler >= nfn:
            raise e100()
        if r.method not in (1, 2, 3, 4, 5):
            raise e100()
        if art.consts[r.module].tag != TAG_STR or art.consts[r.path].tag != TAG_STR:
            raise e100()
        if len(r.params) != art.funcs[r.handler].arity:
            raise e100()
        names = []
        for ix in r.params:
            if ix >= nconst or art.consts[ix].tag != TAG_STR:
                raise e100()
            names.append(art.consts[ix].value)
        if len(set(names)) != len(names):
            raise e100()
        path = art.consts[r.path].value if art.consts[r.path].tag == TAG_STR else ""
        for seg in path.split("/"):
            if seg.startswith("{") and seg.endswith("}") and seg[1:-1] not in names:
                raise e100()
    entries = set()
    for eg in art.egress:
        if eg.module >= nconst or eg.origin >= nconst:
            raise e100()
        module, origin = art.const_str(eg.module), art.const_str(eg.origin)
        from gopyt.check import normalize_origin

        if normalize_origin(origin) != origin or (module, origin) in entries:
            raise e100()
        entries.add((module, origin))
    previous = ""
    for entry in art.evolve:
        if entry.module >= nconst:
            raise e100()
        module = art.const_str(entry.module)
        if module <= previous or not all(0 < v <= 2147483647 for v in
                (entry.max_candidates, entry.timeout_ms, entry.reservoir)):
            raise e100()
        previous = module
    from gopyt.natives import verify_natives

    verify_natives(art)


def _decode_ops(f: Func) -> list[tuple[int, int, tuple]]:
    """Walk instructions; every operand must fit inside the code block."""
    out = []
    pc = 0
    code = f.code
    n = len(code)
    while pc < n:
        op = code[pc]
        fmt = ops.OPERANDS.get(op)
        if fmt is None:
            raise e100()
        cur = pc + 1
        args: list[int] = []
        if fmt == "PARALLEL":
            if cur + 8 > n:
                raise e100()
            count = struct.unpack("<H", code[cur : cur + 2])[0]
            mx = struct.unpack("<H", code[cur + 2 : cur + 4])[0]
            timeout = struct.unpack("<I", code[cur + 4 : cur + 8])[0]
            cur += 8
            if cur + 4 * count > n or count < 1:
                raise e100()
            ids = [struct.unpack("<I", code[cur + 4 * i : cur + 4 * i + 4])[0] for i in range(count)]
            cur += 4 * count
            args = [count, mx, timeout, *ids]
        else:
            for ch in fmt:
                size = {"B": 1, "H": 2, "I": 4, "i": 4}[ch]
                if cur + size > n:
                    raise e100()
                if ch == "B":
                    args.append(code[cur])
                elif ch == "H":
                    args.append(struct.unpack("<H", code[cur : cur + 2])[0])
                elif ch == "I":
                    args.append(struct.unpack("<I", code[cur : cur + 4])[0])
                else:
                    args.append(struct.unpack("<i", code[cur : cur + 4])[0])
                cur += size
        out.append((pc, op, tuple(args)))
        pc = cur
    return out


def _validate_code(art: Artifact, f: Func) -> None:
    insts = _decode_ops(f)
    by_pc = {pc: (op, args) for pc, op, args in insts}
    boundaries = {pc for pc, _op, _a in insts}
    boundaries.add(len(f.code))
    f.boundaries = frozenset(boundaries)
    next_pc = {}
    for i, (pc, _op, _a) in enumerate(insts):
        next_pc[pc] = insts[i + 1][0] if i + 1 < len(insts) else len(f.code)
    heights: dict[int, int] = {}
    for pc, op, args in insts:
        # Validate every operand, including unreachable instructions. The large
        # height suppresses only underflow here; reachable paths check it below.
        _step(art, f, op, args, 65536)
        if op in (ops.JUMP, ops.JUMP_IF_FALSE, ops.JUMP_IF_TRUE):
            target = next_pc[pc] + args[0]
            if target not in boundaries or target == len(f.code):
                raise e100()
    # Abstract stack walk: heights must agree at every join.
    work = [(0, 0)]
    seen: dict[int, int] = {}
    while work:
        pc, height = work.pop()
        while True:
            if pc == len(f.code):
                raise e100()
            if pc in seen:
                if seen[pc] != height:
                    raise e100()
                break
            seen[pc] = height
            if pc not in by_pc:
                raise e100()
            op, args = by_pc[pc]
            height = _step(art, f, op, args, height)
            if op in (ops.HALT, ops.RETURN, ops.TRAP):
                # RETURN pops the result, so a well-formed frame is empty after
                # it; anything left is a lowering bug, not a runtime condition.
                if op in (ops.HALT, ops.RETURN) and height != 0:
                    raise e100()
                break
            nxt = next_pc[pc]
            if op == ops.JUMP:
                pc = nxt + args[0]
                continue
            if op in (ops.JUMP_IF_FALSE, ops.JUMP_IF_TRUE):
                work.append((nxt + args[0], height))
            pc = nxt
    from gopyt.verify import validate_types

    validate_types(art, f, insts)


def _step(art: Artifact, f: Func, op: int, args: tuple, height: int) -> int:
    pops, pushes = 0, 0
    if op == ops.CONST and args[0] >= len(art.consts):
        raise e100()
    if op == ops.TRAP and not (1 <= args[0] <= 14):
        raise e100()
    if op in ops.STACK:
        pops, pushes = ops.STACK[op]
    elif op == ops.CONST:
        if args[0] >= len(art.consts):
            raise e100()
        pops, pushes = 0, 1
    elif op in (ops.LOAD_LOCAL, ops.STORE_LOCAL, ops.GET_FIELD):
        pops, pushes = ops.STACK[op]
    elif op == ops.NEW_RECORD:
        if (args[0] >= len(art.types) or art.types[args[0]].kind != 1
                or args[1] != len(art.types[args[0]].fields)):
            raise e100()
        pops, pushes = args[1], 1
    elif op == ops.NEW_ENUM:
        if (args[0] >= len(art.types) or art.types[args[0]].kind != 2
                or args[1] >= len(art.types[args[0]].variants)
                or args[2] != len(art.types[args[0]].variants[args[1]][1])):
            raise e100()
        pops, pushes = args[2], 1
    elif op == ops.NEW_LIST:
        pops, pushes = args[0], 1
    elif op in (ops.CALL_FN, ops.CALL_TASK):
        if args[0] >= len(art.funcs):
            raise e100()
        callee = art.funcs[args[0]]
        if callee.arity != args[1]:
            raise e100()
        if op == ops.CALL_FN and callee.effects != 0:
            raise e100()
        if op == ops.CALL_TASK and f.kind == ops.KIND_FN:
            raise e100()
        pops, pushes = args[1], 1
    elif op == ops.PARALLEL:
        count, mx, timeout = args[0], args[1], args[2]
        if mx < 1 or timeout < 1:
            raise e100()
        if f.kind == ops.KIND_FN:
            raise e100()
        for fid in args[3:]:
            if fid >= len(art.funcs):
                raise e100()
            if art.funcs[fid].kind != ops.KIND_ARM or art.funcs[fid].arity != 1:
                raise e100()
            if art.funcs[fid].effects & ~ops.FFI_BIT & ~f.effects:
                raise e100()
        pops, pushes = count, 1
    elif op in (ops.HALT, ops.NOP, ops.UNIT, ops.SOME, ops.TRAP):
        pops, pushes = ops.STACK.get(op, (0, 0))
        if op == ops.UNIT:
            pops, pushes = 0, 1
        if op == ops.SOME:
            pops, pushes = 1, 1
    if op == ops.RESET_LOCAL and args[0] < f.arity:
        raise e100()
    if op in (ops.LOAD_LOCAL, ops.STORE_LOCAL, ops.RESET_LOCAL) and args[0] >= f.nlocals:
        raise e100()
    if height - pops < 0:
        raise e100()
    return height - pops + pushes
