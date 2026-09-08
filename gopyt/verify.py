"""Abstract value checking for bytecode, including match-branch refinement.

Nominal references stay finite even for recursive records. A value carries a
set of possible runtime shapes; tag tests narrow aliases on each branch.
"""

from dataclasses import dataclass, replace

from gopyt import gobyte as G, ops as O


@dataclass(frozen=True)
class Value:
    shapes: frozenset
    alias: object = None
    projection: object = None
    literal: object = None
    predicate: object = None


def one(tag, *args):
    return frozenset({(tag, *args)})


NONE = one(0)
BOOL = one(G.TE_BOOL)
INT = one(G.TE_I64)
STR = one(G.TE_STR)
UNIT = one(G.TE_UNIT)


class Verifier:
    def __init__(self, art, func):
        self.art = art
        self.func = func
        self.cache = {}
        self.local_types = [self.ty(ix) for ix in func.params + func.locals]

    def ty(self, index):
        if index in self.cache:
            return self.cache[index]
        te = self.art.texprs[index]
        if te.tag == G.TE_NOM:
            td = self.art.types[te.a]
            result = (frozenset((G.TE_NOM, te.a, variant) for variant in range(len(td.variants)))
                      if td.kind == 2 else one(G.TE_NOM, te.a, -1))
        elif te.tag == G.TE_UNION:
            result = frozenset().union(*(self.ty(m) for m in te.members))
        elif te.tag == G.TE_OPT:
            result = NONE | one(G.TE_OPT, self.ty(te.a))
        elif te.tag == G.TE_LIST:
            result = one(G.TE_LIST, self.ty(te.a))
        elif te.tag == G.TE_MAP:
            result = one(G.TE_MAP, self.ty(te.a), self.ty(te.b))
        else:
            result = one(te.tag)
        self.cache[index] = result
        return result

    def fits(self, got, want):
        for shape in got:
            if shape in want:
                continue
            if shape[0] in (G.TE_OPT, G.TE_LIST, G.TE_MAP) and any(
                shape[0] == target[0] and all(self.fits(a, b) for a, b in zip(shape[1:], target[1:]))
                for target in want
            ):
                continue
            return False
        return True

    def need(self, value, shapes):
        if not self.fits(value.shapes, shapes):
            raise G.e100()

    def equality(self, shapes, seen=frozenset()):
        for shape in shapes:
            tag = shape[0]
            if tag in (0, G.TE_BOOL, G.TE_I32, G.TE_I64, G.TE_U32, G.TE_U64,
                       G.TE_STR, G.TE_BYTES, G.TE_UNIT):
                continue
            if tag in (G.TE_OPT, G.TE_LIST, G.TE_MAP):
                if not all(self.equality(part, seen) for part in shape[1:]):
                    return False
            elif tag == G.TE_NOM:
                td = self.art.types[shape[1]]
                if td.kind == 3:
                    return False
                if shape[1] in seen:
                    continue
                fields = td.fields + [field for _name, fields in td.variants for field in fields]
                if not all(self.equality(self.ty(ty), seen | {shape[1]}) for _name, ty in fields):
                    return False
            else:
                return False
        return True

    def merge(self, left, right):
        shapes = left.shapes | right.shapes
        if left.shapes != right.shapes:
            # Different member shapes may join only as a declared type.
            candidates = self.local_types + [self.ty(self.func.ret)]
            candidates += [self.ty(i) for i, te in enumerate(self.art.texprs)
                           if te.tag in (G.TE_UNION, G.TE_OPT, G.TE_NOM)]
            if not any(self.fits(shapes, candidate) for candidate in candidates):
                raise G.e100()
        return Value(shapes,
                     left.alias if left.alias == right.alias else None,
                     left.projection if left.projection == right.projection else None,
                     left.literal if left.literal == right.literal else None,
                     left.predicate if left.predicate == right.predicate else None)

    def refine(self, stack, locals_, predicate, truth):
        if predicate is None:
            return stack, locals_
        mode, alias, expected = predicate
        if alias is None:
            return stack, locals_

        def filter_value(value):
            if value.alias != alias:
                return value
            def matches(shape):
                if mode == "none":
                    return shape[0] == 0
                if mode == "enum":
                    return shape[0] == G.TE_NOM and shape[2] == expected
                actual = shape[1] if shape[0] == G.TE_NOM else {
                    G.TE_BOOL: -1, G.TE_I32: -2, G.TE_I64: -3, G.TE_U32: -4,
                    G.TE_U64: -5, G.TE_STR: -6, G.TE_BYTES: -7, G.TE_UNIT: -8,
                }.get(shape[0])
                return actual == expected
            return replace(value, shapes=frozenset(s for s in value.shapes if matches(s) == truth))

        stack = tuple(filter_value(v) for v in stack)
        locals_ = tuple(filter_value(v) if v is not None else None for v in locals_)
        if any(not v.shapes for v in stack) or any(v is not None and not v.shapes for v in locals_):
            return None
        return stack, locals_

    def run(self, insts):
        code = {pc: (op, args, insts[i + 1][0] if i + 1 < len(insts) else len(self.func.code))
                for i, (pc, op, args) in enumerate(insts)}
        initial = tuple(Value(self.local_types[i], ("param", i)) if i < self.func.arity else None
                        for i in range(self.func.nlocals))
        work = [(0, (), initial)]
        seen = {}
        while work:
            pc, stack, locals_ = work.pop()
            if pc not in code:
                raise G.e100()
            if pc in seen:
                old_stack, old_locals = seen[pc]
                if len(stack) != len(old_stack):
                    raise G.e100()
                stack = tuple(self.merge(a, b) for a, b in zip(old_stack, stack))
                locals_ = tuple(self.merge(a, b) if a is not None and b is not None else None
                                for a, b in zip(old_locals, locals_))
                if (stack, locals_) == seen[pc]:
                    continue
            seen[pc] = stack, locals_
            op, args, nxt = code[pc]
            stack, locals_ = list(stack), list(locals_)

            def pop():
                if not stack:
                    raise G.e100()
                return stack.pop()

            def push(shapes, **metadata):
                stack.append(Value(shapes, alias=("expr", pc), **metadata))

            if op == O.CONST:
                const = self.art.consts[args[0]]
                tag = {G.TAG_I64: G.TE_I64, G.TAG_F64: G.TE_F64, G.TAG_STR: G.TE_STR,
                       G.TAG_BYTES: G.TE_BYTES, G.TAG_BOOL: G.TE_BOOL, G.TAG_UNIT: G.TE_UNIT,
                       G.TAG_NONE: 0, G.TAG_TYPE: G.TE_I64, G.TAG_EFFECT: G.TE_I64,
                       G.TAG_FN: 99}.get(const.tag)
                if tag is None:
                    raise G.e100()
                push(one(tag), literal=const.value)
            elif op == O.UNIT:
                push(UNIT)
            elif op == O.LOAD_LOCAL:
                value = locals_[args[0]]
                if value is None:
                    raise G.e100()
                stack.append(value)
            elif op == O.RESET_LOCAL:
                old = locals_[args[0]]
                if old is not None and old.alias is not None:
                    def forget(value):
                        if value is None:
                            return None
                        return replace(value,
                            alias=None if value.alias == old.alias else value.alias,
                            projection=None if value.projection and value.projection[1] == old.alias else value.projection,
                            predicate=None if value.predicate and value.predicate[1] == old.alias else value.predicate)
                    stack = [forget(value) for value in stack]
                    locals_ = [forget(value) for value in locals_]
                locals_[args[0]] = None
            elif op == O.STORE_LOCAL:
                value = pop()
                self.need(value, self.local_types[args[0]])
                locals_[args[0]] = replace(value, alias=("store", pc))
            elif op == O.POP:
                pop()
            elif op == O.DUP:
                value = pop()
                stack.extend((value, value))
            elif op == O.SOME:
                push(one(G.TE_OPT, pop().shapes))
            elif op == O.IS_NONE:
                value = pop()
                if any(s[0] not in (0, G.TE_OPT) for s in value.shapes):
                    raise G.e100()
                push(BOOL, predicate=("none", value.alias, None))
            elif op == O.SOME_VALUE:
                value = pop()
                if any(s[0] != G.TE_OPT for s in value.shapes):
                    raise G.e100()
                push(frozenset().union(*(s[1] for s in value.shapes)))
            elif op in (O.NEW_RECORD, O.NEW_ENUM):
                td = self.art.types[args[0]]
                variant = args[1] if op == O.NEW_ENUM else -1
                fields = td.fields if variant == -1 else td.variants[variant][1]
                for _name, ty in reversed(fields):
                    self.need(pop(), self.ty(ty))
                push(one(G.TE_NOM, args[0], variant))
            elif op == O.GET_FIELD:
                value = pop()
                result = frozenset()
                for shape in value.shapes:
                    if shape[0] != G.TE_NOM:
                        raise G.e100()
                    td = self.art.types[shape[1]]
                    fields = td.fields if shape[2] == -1 else td.variants[shape[2]][1]
                    if td.kind == 3 or args[0] >= len(fields):
                        raise G.e100()
                    result |= self.ty(fields[args[0]][1])
                push(result)
            elif op in (O.ENUM_TAG, O.VALUE_TYPE):
                value = pop()
                if op == O.ENUM_TAG and any(s[0] != G.TE_NOM or s[2] < 0 for s in value.shapes):
                    raise G.e100()
                push(INT, projection=("enum" if op == O.ENUM_TAG else "type", value.alias))
            elif op in (O.EQ, O.NE, O.EQ_STR):
                right, left = pop(), pop()
                if not self.equality(left.shapes | right.shapes):
                    raise G.e100()
                if op == O.EQ_STR:
                    self.need(left, STR)
                    self.need(right, STR)
                predicate = None
                if op == O.EQ:
                    if left.projection is not None and type(right.literal) is int:
                        predicate = (*left.projection, right.literal)
                    elif right.projection is not None and type(left.literal) is int:
                        predicate = (*right.projection, left.literal)
                push(BOOL, predicate=predicate)
            elif op in (O.ADD_I64, O.SUB_I64, O.MUL_I64, O.DIV_I64, O.MOD_I64,
                        O.LT_I64, O.LE_I64, O.GT_I64, O.GE_I64, O.AND, O.OR):
                want = BOOL if op in (O.AND, O.OR) else INT
                self.need(pop(), want)
                self.need(pop(), want)
                push(BOOL if op in (O.LT_I64, O.LE_I64, O.GT_I64, O.GE_I64, O.AND, O.OR) else INT)
            elif op in (O.NEG_I64, O.NOT):
                want = BOOL if op == O.NOT else INT
                self.need(pop(), want)
                push(want)
            elif op in (O.REQUIRE, O.ENSURE):
                self.need(pop(), BOOL)
            elif op in (O.CALL_FN, O.CALL_TASK):
                callee = self.art.funcs[args[0]]
                for ty in reversed(callee.params):
                    self.need(pop(), self.ty(ty))
                push(self.ty(callee.ret))
            elif op == O.NEW_LIST:
                values = [pop().shapes for _ in range(args[0])]
                push(one(G.TE_LIST, frozenset().union(*values)))
            elif op in (O.LIST_LEN, O.LIST_GET, O.LIST_APPEND):
                item = pop() if op in (O.LIST_GET, O.LIST_APPEND) else None
                value = pop()
                if any(s[0] != G.TE_LIST for s in value.shapes):
                    raise G.e100()
                elems = frozenset().union(*(s[1] for s in value.shapes))
                if op == O.LIST_GET:
                    self.need(item, INT)
                    push(elems)
                elif op == O.LIST_LEN:
                    push(INT)
                else:
                    if elems:
                        self.need(item, elems)
                    push(one(G.TE_LIST, elems | item.shapes))
            elif op == O.NEW_MAP:
                push(one(G.TE_MAP, frozenset(), frozenset()))
            elif op in (O.MAP_GET, O.MAP_SET):
                item = pop() if op == O.MAP_SET else None
                key, value = pop(), pop()
                if any(s[0] != G.TE_MAP for s in value.shapes):
                    raise G.e100()
                keys = frozenset().union(*(s[1] for s in value.shapes))
                vals = frozenset().union(*(s[2] for s in value.shapes))
                self.need(key, keys or (BOOL | INT | STR))
                if item is None:
                    push(NONE | one(G.TE_OPT, vals))
                else:
                    if vals:
                        self.need(item, vals)
                    push(one(G.TE_MAP, keys | key.shapes, vals | item.shapes))
            elif op == O.PARALLEL:
                results = frozenset()
                for fid in reversed(args[3:]):
                    arm = self.art.funcs[fid]
                    self.need(pop(), self.ty(arm.params[0]))
                    ret = self.ty(arm.ret)
                    if results and results != ret:
                        raise G.e100()
                    results = ret
                push(one(G.TE_LIST, results))
            elif op == O.RETURN:
                self.need(pop(), self.ty(self.func.ret))
                continue
            elif op in (O.HALT, O.TRAP):
                continue
            elif op not in (O.NOP, O.JUMP, O.JUMP_IF_FALSE, O.JUMP_IF_TRUE):
                raise G.e100()

            if op in (O.JUMP_IF_FALSE, O.JUMP_IF_TRUE):
                value = pop()
                self.need(value, BOOL)
                for truth, target in ((op == O.JUMP_IF_TRUE, nxt + args[0]),
                                      (op != O.JUMP_IF_TRUE, nxt)):
                    narrowed = self.refine(stack, locals_, value.predicate, truth)
                    if narrowed is not None:
                        work.append((target, *narrowed))
            else:
                target = nxt + args[0] if op == O.JUMP else nxt
                work.append((target, tuple(stack), tuple(locals_)))


def validate_types(art, func, insts):
    Verifier(art, func).run(insts)
