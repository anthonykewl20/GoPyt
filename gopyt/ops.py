"""The one v0 instruction set (docs/bytecode.md). Agents do not invent opcodes."""

from __future__ import annotations

NOP = 0x00
HALT = 0x01
TRAP = 0x02
POP = 0x03
DUP = 0x04
CONST = 0x10
SOME = 0x11
UNIT = 0x12
LOAD_LOCAL = 0x20
STORE_LOCAL = 0x21
RESET_LOCAL = 0x22
GET_FIELD = 0x30
NEW_RECORD = 0x31
NEW_ENUM = 0x32
ENUM_TAG = 0x33
VALUE_TYPE = 0x34
IS_NONE = 0x35
SOME_VALUE = 0x36
NEW_LIST = 0x40
LIST_LEN = 0x41
LIST_GET = 0x42
LIST_APPEND = 0x43
NEW_MAP = 0x44
MAP_GET = 0x45
MAP_SET = 0x46
ADD_I64 = 0x50
SUB_I64 = 0x51
MUL_I64 = 0x52
DIV_I64 = 0x53
MOD_I64 = 0x54
NEG_I64 = 0x55
EQ = 0x60
NE = 0x61
LT_I64 = 0x62
LE_I64 = 0x63
GT_I64 = 0x64
GE_I64 = 0x65
EQ_STR = 0x66
NOT = 0x70
AND = 0x71
OR = 0x72
JUMP = 0x80
JUMP_IF_FALSE = 0x81
JUMP_IF_TRUE = 0x82
CALL_FN = 0x90
CALL_TASK = 0x91
RETURN = 0x92
REQUIRE = 0xA0
ENSURE = 0xA1
PARALLEL = 0xB0

# name -> (operand format, stack pop, stack push). "*" means variable.
OPERANDS: dict[int, str] = {
    NOP: "",
    HALT: "",
    TRAP: "H",
    POP: "",
    DUP: "",
    CONST: "I",
    SOME: "",
    UNIT: "",
    LOAD_LOCAL: "H",
    STORE_LOCAL: "H",
    RESET_LOCAL: "H",
    GET_FIELD: "H",
    NEW_RECORD: "IH",
    NEW_ENUM: "IHH",
    ENUM_TAG: "",
    VALUE_TYPE: "",
    IS_NONE: "",
    SOME_VALUE: "",
    NEW_LIST: "H",
    LIST_LEN: "",
    LIST_GET: "",
    LIST_APPEND: "",
    NEW_MAP: "",
    MAP_GET: "",
    MAP_SET: "",
    ADD_I64: "",
    SUB_I64: "",
    MUL_I64: "",
    DIV_I64: "",
    MOD_I64: "",
    NEG_I64: "",
    EQ: "",
    NE: "",
    LT_I64: "",
    LE_I64: "",
    GT_I64: "",
    GE_I64: "",
    EQ_STR: "",
    NOT: "",
    AND: "",
    OR: "",
    JUMP: "i",
    JUMP_IF_FALSE: "i",
    JUMP_IF_TRUE: "i",
    CALL_FN: "IH",
    CALL_TASK: "IH",
    RETURN: "",
    REQUIRE: "",
    ENSURE: "",
    PARALLEL: "PARALLEL",
}

NAMES = {v: k for k, v in list(globals().items()) if isinstance(v, int) and k.isupper()}

# Stack effect for fixed-shape opcodes: (pops, pushes).
STACK: dict[int, tuple[int, int]] = {
    NOP: (0, 0),
    TRAP: (0, 0),
    POP: (1, 0),
    DUP: (1, 2),
    CONST: (0, 1),
    SOME: (1, 1),
    UNIT: (0, 1),
    LOAD_LOCAL: (0, 1),
    STORE_LOCAL: (1, 0),
    RESET_LOCAL: (0, 0),
    GET_FIELD: (1, 1),
    ENUM_TAG: (1, 1),
    VALUE_TYPE: (1, 1),
    IS_NONE: (1, 1),
    SOME_VALUE: (1, 1),
    LIST_LEN: (1, 1),
    LIST_GET: (2, 1),
    LIST_APPEND: (2, 1),
    NEW_MAP: (0, 1),
    MAP_GET: (2, 1),
    MAP_SET: (3, 1),
    ADD_I64: (2, 1),
    SUB_I64: (2, 1),
    MUL_I64: (2, 1),
    DIV_I64: (2, 1),
    MOD_I64: (2, 1),
    NEG_I64: (1, 1),
    EQ: (2, 1),
    NE: (2, 1),
    LT_I64: (2, 1),
    LE_I64: (2, 1),
    GT_I64: (2, 1),
    GE_I64: (2, 1),
    EQ_STR: (2, 1),
    NOT: (1, 1),
    AND: (2, 1),
    OR: (2, 1),
    JUMP_IF_FALSE: (1, 0),
    JUMP_IF_TRUE: (1, 0),
    RETURN: (1, 0),
    REQUIRE: (1, 0),
    ENSURE: (1, 0),
}

# Trap codes (docs/bytecode.md + docs/implementer.md 11).
TRAP_REQUIRES = 1
TRAP_ENSURES = 2
TRAP_OVERFLOW = 3
TRAP_DIV_ZERO = 4
TRAP_INDEX = 5
TRAP_TIMEOUT = 6
TRAP_PAR_MAX = 7
TRAP_EFFECT = 8
TRAP_TYPE = 9
TRAP_DOUBLE_STORE = 10
TRAP_OPCODE = 11
TRAP_DEPTH = 12
TRAP_ASSERT = 13
TRAP_ALLOC = 14

EFFECT_BITS = {
    "network": 0,
    "filesystem.read": 1,
    "filesystem.write": 2,
    "database.read": 3,
    "database.write": 4,
    "time": 5,
    "random": 6,
    "log": 7,
    "model": 8,
    "ffi": 9,
    "secret": 10,
    "observe": 11,
}

KIND_FN = 1
KIND_TASK = 2
KIND_WORKFLOW = 3
KIND_TEST = 4
KIND_ARM = 5
KIND_NATIVE = 6

METHOD_NUM = {"get": 1, "post": 2, "put": 3, "patch": 4, "delete": 5}

# S7 (amended) admits scalar union members and grammar.ebnf gives them a
# pattern, but bytecode.md still types VALUE_TYPE as "nominal -> i64". Until
# that document is amended, VALUE_TYPE is total: a scalar yields one of these
# reserved ids, which can never collide with a type-table index (0..n-1).
SCALAR_TYPE_IDS = {
    "bool": -1,
    "i32": -2,
    "i64": -3,
    "u32": -4,
    "u64": -5,
    "str": -6,
    "bytes": -7,
    "unit": -8,
}

FFI_BIT = 1 << 9  # S9 (amended): stdlib `ffi` does not propagate to callers

MAX_CALL_DEPTH = 256
MAX_ALLOC = 2_147_483_647


def effect_mask(effects) -> int:
    mask = 0
    for e in effects:
        mask |= 1 << EFFECT_BITS[e]
    return mask


def mask_effects(mask: int) -> set[str]:
    return {name for name, bit in EFFECT_BITS.items() if mask & (1 << bit)}
