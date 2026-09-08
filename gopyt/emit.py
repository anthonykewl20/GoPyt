"""Lowering: typed program -> build/out.gobyte (docs/bytecode.md).

Deterministic emit order is fixed by docs/implementer.md 10: type ids by
qualified name, fn ids by compiler symbol, pools in first-seen traversal order.
"""

from __future__ import annotations

import struct

from gopyt import ops
from gopyt.ast_nodes import *
from gopyt.check import (
    CallRes,
    CaptureRes,
    ConstructRes,
    FieldPathRes,
    FuncCheck,
    LocalRes,
    MatchRes,
    ParallelRes,
    Program,
    inst_key,
)
from gopyt.diag import CompileError, Diag
from gopyt.gobyte import (
    PRIM_TAG,
    TAG_BOOL,
    TAG_F64,
    TAG_I64,
    TAG_NONE,
    TAG_STR,
    TAG_TYPE,
    TE_LIST,
    TE_MAP,
    TE_NOM,
    TE_OPT,
    TE_UNION,
    Artifact,
    Const,
    EgressEntry,
    EvolveEntry,
    Func,
    Route,
    TExpr,
    TypeDef,
)
from gopyt.types import ListT, MapT, Nom, NoneTy, Opt, Prim, Ty, Union

KINDS = {
    "fn": ops.KIND_FN,
    "task": ops.KIND_TASK,
    "workflow": ops.KIND_WORKFLOW,
    "test": ops.KIND_TEST,
    "arm": ops.KIND_ARM,
    "native": ops.KIND_NATIVE,
}


def _sort_key_type(nom: Nom) -> tuple[bytes, bytes]:
    args = ", ".join(a.key for a in nom.args)
    return (nom.name.encode("utf-8"), args.encode("utf-8"))


def _sort_key_fn(fc: FuncCheck) -> tuple[bytes, bytes]:
    args = ", ".join(a.key for a in fc.targs)
    return (fc.symbol.encode("utf-8"), args.encode("utf-8"))


class Emitter:
    def __init__(self, prog: Program) -> None:
        self.prog = prog
        self.art = Artifact()
        self.const_ix: dict[tuple, int] = {}
        self.texpr_ix: dict[str, int] = {}
        self.noms = sorted(prog.noms.values(), key=_sort_key_type)
        self.type_id = {n.key: i for i, n in enumerate(self.noms)}
        self.fns = sorted(prog.funcs.values(), key=_sort_key_fn)
        self.fn_id = {f.key: i for i, f in enumerate(self.fns)}

    # -- pools -----------------------------------------------------------

    def const(self, tag: int, value: object = None) -> int:
        key = (tag, value if not isinstance(value, bytes) else bytes(value))
        hit = self.const_ix.get(key)
        if hit is not None:
            return hit
        self.art.consts.append(Const(tag, value))
        self.const_ix[key] = len(self.art.consts) - 1
        return self.const_ix[key]

    def texpr(self, ty: Ty) -> int:
        hit = self.texpr_ix.get(ty.key)
        if hit is not None:
            return hit
        if isinstance(ty, Prim):
            entry = TExpr(PRIM_TAG[ty.name])
        elif isinstance(ty, Opt):
            entry = TExpr(TE_OPT, self.texpr(ty.elem))
        elif isinstance(ty, ListT):
            entry = TExpr(TE_LIST, self.texpr(ty.elem))
        elif isinstance(ty, MapT):
            entry = TExpr(TE_MAP, self.texpr(ty.key_ty), self.texpr(ty.val_ty))
        elif isinstance(ty, Nom):
            entry = TExpr(TE_NOM, self.type_id[ty.key])
        elif isinstance(ty, Union):
            members = tuple(self.texpr(m) for m in ty.members)
            entry = TExpr(TE_UNION, members=members)
        elif isinstance(ty, NoneTy):
            entry = TExpr(PRIM_TAG["unit"])
        else:
            raise CompileError(Diag(1, None, None, 0))
        hit = self.texpr_ix.get(ty.key)
        if hit is not None:
            return hit
        self.art.texprs.append(entry)
        self.texpr_ix[ty.key] = len(self.art.texprs) - 1
        return self.texpr_ix[ty.key]

    # -- build -----------------------------------------------------------

    def build(self) -> Artifact:
        for nom in self.noms:
            info = self.prog.types[nom.name]
            name_ix = self.const(TAG_STR, nom.key)
            if info.kind == "record":
                td = TypeDef(1, name_ix)
                for fname, fty in info.field_types(nom.args):
                    td.fields.append((self.const(TAG_STR, fname), self.texpr(fty)))
            elif info.kind == "enum":
                td = TypeDef(2, name_ix)
                for i, (vname, _f) in enumerate(info.variants):
                    fields = [
                        (self.const(TAG_STR, fn), self.texpr(ft))
                        for fn, ft in info.variant_fields(i, nom.args)
                    ]
                    td.variants.append((self.const(TAG_STR, vname), fields))
            else:
                td = TypeDef(3, name_ix)
            self.art.types.append(td)
        for fc in self.fns:
            self.art.funcs.append(self.func(fc))
        for module, verb, path, handler in sorted(
            self.prog.routes, key=lambda r: (r[0].encode(), ops.METHOD_NUM[r[1]], r[2].encode())
        ):
            self.art.routes.append(
                Route(
                    self.const(TAG_STR, module),
                    ops.METHOD_NUM[verb],
                    self.const(TAG_STR, path),
                    self.fn_id[handler],
                    [self.const(TAG_STR, n) for n, _t in self.prog.funcs[handler].params],
                )
            )
        for module, origin in sorted(self.prog.egress, key=lambda e: (e[0].encode(), e[1].encode())):
            self.art.egress.append(
                EgressEntry(self.const(TAG_STR, module), self.const(TAG_STR, origin))
            )
        for module, bounds in sorted(self.prog.evolve.items()):
            self.art.evolve.append(EvolveEntry(self.const(TAG_STR, module), *bounds))
        return self.art

    def func(self, fc: FuncCheck) -> Func:
        kind = KINDS[fc.kind]
        # bytecode.md: a kind-6 entry carries the exact stdlib callable name, so
        # the VM can dispatch and verify it. Others carry the instantiation key.
        name_ix = self.const(TAG_STR, fc.symbol if kind == ops.KIND_NATIVE else fc.key)
        params = [self.texpr(t) for _n, t in fc.params]
        ret = self.texpr(fc.ret)
        # A native carries no frame, so its slot count is exactly its arity.
        slots = fc.local_types if fc.local_types else [t for _n, t in fc.params]
        locals_ = [self.texpr(t) for t in slots[len(fc.params) :]]
        effects = ops.effect_mask(fc.effects)
        code = b"" if kind == ops.KIND_NATIVE else FnEmitter(self, fc).run()
        return Func(
            name=name_ix,
            kind=kind,
            arity=len(fc.params),
            nlocals=len(slots),
            effects=effects,
            params=params,
            ret=ret,
            locals=locals_,
            code=code,
        )


class FnEmitter:
    def __init__(self, em: Emitter, fc: FuncCheck) -> None:
        self.em = em
        self.fc = fc
        self.buf = bytearray()
        self.loop_depth = 0

    # -- primitive emit --------------------------------------------------

    def op(self, code: int, *args: int) -> None:
        if code == ops.STORE_LOCAL and self.loop_depth:
            self.op(ops.RESET_LOCAL, args[0])
        self.buf.append(code)
        fmt = ops.OPERANDS[code]
        for ch, value in zip(fmt, args):
            if ch == "B":
                self.buf += struct.pack("<B", value)
            elif ch == "H":
                self.buf += struct.pack("<H", value)
            elif ch == "I":
                self.buf += struct.pack("<I", value)
            else:
                self.buf += struct.pack("<i", value)

    def jump(self, code: int) -> int:
        self.buf.append(code)
        pos = len(self.buf)
        self.buf += b"\x00\x00\x00\x00"
        return pos

    def patch(self, pos: int) -> None:
        struct.pack_into("<i", self.buf, pos, len(self.buf) - (pos + 4))

    def patch_to(self, pos: int, target: int) -> None:
        struct.pack_into("<i", self.buf, pos, target - (pos + 4))

    def here(self) -> int:
        return len(self.buf)

    # -- entry -----------------------------------------------------------

    def run(self) -> bytes:
        for c in self.fc.contracts:
            if c.open_id is None and c.kind == "requires":
                self.expr(c.expr)
                self.op(ops.REQUIRE)
        if self.fc.kind == "arm":
            self.expr(self.fc.body)
            self.op(ops.RETURN)
            return bytes(self.buf)
        self.block(self.fc.body)
        return bytes(self.buf)

    def block(self, b: Block) -> None:
        for st in b.stmts:
            self.stmt(st)

    def stmt(self, st: object) -> None:
        if isinstance(st, BindStmt):
            self.expr(st.expr)
            self.op(ops.STORE_LOCAL, self.fc.slot[id(st)])
            return
        if isinstance(st, ReturnStmt):
            self.expr(st.expr)
            if self.fc.result_slot >= 0:
                self.op(ops.STORE_LOCAL, self.fc.result_slot)
                for c in self.fc.contracts:
                    if c.open_id is None and c.kind == "ensures":
                        self.expr(c.expr)
                        self.op(ops.ENSURE)
                self.op(ops.LOAD_LOCAL, self.fc.result_slot)
            self.op(ops.RETURN)
            return
        if isinstance(st, ExprStmt):
            self.expr(st.expr)
            self.op(ops.POP)
            return
        if isinstance(st, IfStmt):
            self.expr(st.cond)
            to_else = self.jump(ops.JUMP_IF_FALSE)
            self.block(st.then)
            if st.els is not None:
                to_end = None if self.returns(st.then) else self.jump(ops.JUMP)
                self.patch(to_else)
                self.block(st.els)
                if to_end is not None:
                    self.patch(to_end)
            else:
                self.patch(to_else)
            return
        if isinstance(st, ForStmt):
            self.for_stmt(st)
            return
        raise CompileError(Diag(1, None, None, 0))

    @staticmethod
    def returns(block: Block) -> bool:
        if not block.stmts:
            return False
        last = block.stmts[-1]
        return isinstance(last, ReturnStmt) or (
            isinstance(last, IfStmt) and last.els is not None
            and FnEmitter.returns(last.then) and FnEmitter.returns(last.els)
        )

    def for_stmt(self, st: ForStmt) -> None:
        self.loop_depth += 1
        item_slot = self.fc.slot[id(st)]
        list_slot = self.fc.for_list[id(st)]
        idx_slot = self.fc.for_idx[id(st)]
        self.expr(st.iter)
        self.op(ops.STORE_LOCAL, list_slot)
        self.op(ops.CONST, self.em.const(TAG_I64, 0))
        self.op(ops.STORE_LOCAL, idx_slot)
        head = self.here()
        self.op(ops.LOAD_LOCAL, idx_slot)
        self.op(ops.LOAD_LOCAL, list_slot)
        self.op(ops.LIST_LEN)
        self.op(ops.LT_I64)
        to_end = self.jump(ops.JUMP_IF_FALSE)
        self.op(ops.LOAD_LOCAL, list_slot)
        self.op(ops.LOAD_LOCAL, idx_slot)
        self.op(ops.LIST_GET)
        self.op(ops.STORE_LOCAL, item_slot)
        self.block(st.body)
        self.op(ops.LOAD_LOCAL, idx_slot)
        self.op(ops.CONST, self.em.const(TAG_I64, 1))
        self.op(ops.ADD_I64)
        self.op(ops.STORE_LOCAL, idx_slot)
        back = self.jump(ops.JUMP)
        self.patch_to(back, head)
        self.patch(to_end)
        self.loop_depth -= 1

    # -- expressions -----------------------------------------------------

    def ty(self, e: object) -> Ty:
        return self.fc.ty[id(e)]

    def expr(self, e: object) -> None:
        if isinstance(e, IntLit):
            self.op(ops.CONST, self.em.const(TAG_I64, int(e.value)))
            return
        if isinstance(e, FloatLit):
            self.op(ops.CONST, self.em.const(TAG_F64, float(e.value)))
            return
        if isinstance(e, StrLit):
            from gopyt.check import _unquote

            self.op(ops.CONST, self.em.const(TAG_STR, _unquote(e.raw)))
            return
        if isinstance(e, BoolLit):
            self.op(ops.CONST, self.em.const(TAG_BOOL, e.value))
            return
        if isinstance(e, NoneLit):
            self.op(ops.CONST, self.em.const(TAG_NONE))
            return
        if isinstance(e, UnitLit):
            self.op(ops.UNIT)
            return
        if isinstance(e, SomeExpr):
            self.expr(e.inner)
            self.op(ops.SOME)
            return
        if isinstance(e, NameExpr):
            res = self.fc.res.get(id(e))
            if isinstance(res, LocalRes):
                self.op(ops.LOAD_LOCAL, res.slot)
                return
            if isinstance(res, CaptureRes):
                self.op(ops.LOAD_LOCAL, 0)
                self.op(ops.GET_FIELD, res.index)
                return
            if isinstance(res, FieldPathRes):
                self.load_base(res.base)
                for index in res.indices:
                    self.op(ops.GET_FIELD, index)
                return
            if isinstance(res, ConstructRes):
                self.construct(res, [])
                return
            raise CompileError(Diag(1, None, None, 0))
        if isinstance(e, FieldExpr):
            self.expr(e.base)
            self.op(ops.GET_FIELD, self.fc.res[id(e)].index)
            return
        if isinstance(e, ConstructExpr):
            self.construct(self.fc.res[id(e)], e.fields)
            return
        if isinstance(e, CallExpr):
            res: CallRes = self.fc.res[id(e)]
            for arg in e.args:
                self.expr(arg)
            fid = self.em.fn_id[res.key]
            callee = self.em.fns[fid]
            if callee.effects:
                self.op(ops.CALL_TASK, fid, len(e.args))
            else:
                self.op(ops.CALL_FN, fid, len(e.args))
            return
        if isinstance(e, BinExpr):
            self.binary(e)
            return
        if isinstance(e, UnaryExpr):
            self.expr(e.inner)
            self.op(ops.NOT if e.op == "not" else ops.NEG_I64)
            return
        if isinstance(e, MatchExpr):
            self.match(e)
            return
        if isinstance(e, ParallelExpr):
            self.parallel(e)
            return
        raise CompileError(Diag(1, None, None, 0))

    def load_base(self, res: object) -> None:
        if isinstance(res, LocalRes):
            self.op(ops.LOAD_LOCAL, res.slot)
            return
        if isinstance(res, CaptureRes):
            self.op(ops.LOAD_LOCAL, 0)
            self.op(ops.GET_FIELD, res.index)
            return
        raise CompileError(Diag(1, None, None, 0))

    def construct(self, res: ConstructRes, given: list) -> None:
        type_id = self.em.type_id[res.nom.key]
        for i in res.order:
            self.expr(given[i][1])
        if res.kind == "record":
            self.op(ops.NEW_RECORD, type_id, len(res.order))
        else:
            self.op(ops.NEW_ENUM, type_id, res.variant, len(res.order))

    def binary(self, e: BinExpr) -> None:
        if e.op in ("and", "or"):
            self.expr(e.left)
            self.op(ops.DUP)
            skip = self.jump(ops.JUMP_IF_FALSE if e.op == "and" else ops.JUMP_IF_TRUE)
            self.op(ops.POP)
            self.expr(e.right)
            self.patch(skip)
            return
        self.expr(e.left)
        self.expr(e.right)
        lt = self.ty(e.left)
        if e.op in ("==", "!="):
            if lt.key == "str":
                self.op(ops.EQ_STR)
                if e.op == "!=":
                    self.op(ops.NOT)
            else:
                self.op(ops.EQ if e.op == "==" else ops.NE)
            return
        table = {
            "<": ops.LT_I64,
            "<=": ops.LE_I64,
            ">": ops.GT_I64,
            ">=": ops.GE_I64,
            "+": ops.ADD_I64,
            "-": ops.SUB_I64,
            "*": ops.MUL_I64,
            "/": ops.DIV_I64,
            "%": ops.MOD_I64,
        }
        self.op(table[e.op])

    # -- match -----------------------------------------------------------

    def match(self, e: MatchExpr) -> None:
        res: MatchRes = self.fc.res[id(e)]
        self.expr(e.scrut)
        if res.mode == "optional":
            self.match_optional(e, res)
            return
        self.op(ops.DUP)
        self.op(ops.ENUM_TAG if res.mode == "enum" else ops.VALUE_TYPE)
        ends: list[int] = []
        for arm, ar in zip(e.arms, res.arms):
            if res.mode == "enum":
                tag_const = self.em.const(TAG_I64, ar.tag)
            elif ar.scalar is not None:
                tag_const = self.em.const(TAG_I64, ops.SCALAR_TYPE_IDS[ar.scalar])
            else:
                tag_const = self.em.const(TAG_TYPE, self.em.type_id[ar.nom.key])
            self.op(ops.DUP)
            self.op(ops.CONST, tag_const)
            self.op(ops.EQ)
            nxt = self.jump(ops.JUMP_IF_FALSE)
            self.op(ops.POP)  # drop the tag
            for slot, index in ar.binds:
                self.op(ops.DUP)
                self.op(ops.GET_FIELD, index)
                self.op(ops.STORE_LOCAL, slot)
            self.op(ops.POP)  # drop the scrutinee
            self.expr(arm.body)
            ends.append(self.jump(ops.JUMP))
            self.patch(nxt)
        self.op(ops.POP)
        self.op(ops.POP)
        self.op(ops.TRAP, ops.TRAP_TYPE)
        for pos in ends:
            self.patch(pos)

    def match_optional(self, e: MatchExpr, res: MatchRes) -> None:
        arms = {ar.tag: (arm, ar) for arm, ar in zip(e.arms, res.arms)}
        none_arm, none_res = arms[0]
        some_arm, some_res = arms[1]
        self.op(ops.DUP)
        self.op(ops.IS_NONE)
        to_some = self.jump(ops.JUMP_IF_FALSE)
        self.op(ops.POP)
        self.expr(none_arm.body)
        to_end = self.jump(ops.JUMP)
        self.patch(to_some)
        self.op(ops.SOME_VALUE)
        self.op(ops.STORE_LOCAL, some_res.binds[0][0])
        self.expr(some_arm.body)
        self.patch(to_end)

    # -- parallel --------------------------------------------------------

    def parallel(self, e: ParallelExpr) -> None:
        res: ParallelRes = self.fc.res[id(e)]
        ids = []
        for index, arm in enumerate(res.arms):
            for source in arm.captures:
                self.load_base(source)
            cap_nom = Nom(f"capture:{self.fc.key}:{index}")
            self.op(ops.NEW_RECORD, self.em.type_id[cap_nom.key], len(arm.captures))
            ids.append(self.em.fn_id[inst_key(arm.symbol, ())])
        self.buf.append(ops.PARALLEL)
        self.buf += struct.pack("<HHI", len(res.arms), e.max_n, e.timeout_ms)
        for fid in ids:
            self.buf += struct.pack("<I", fid)


def emit_artifact(prog: Program) -> Artifact:
    return Emitter(prog).build()


def emit_program(prog: Program) -> tuple[Artifact, dict[str, int]]:
    em = Emitter(prog)
    return em.build(), em.fn_id
