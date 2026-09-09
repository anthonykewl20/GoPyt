# GoPyT v0 bytecode

Amended by [the implementation closure amendment](runtime-amendment-2026-09-05.md).

Must follow: `docs/charter.md`, `docs/spec.md`, `docs/grammar.ebnf`.

The VM is a stack machine with a tracing GC (D12). There is **one** instruction set. Agents do not invent opcodes. Unknown opcode = `GOPYT_E100 opcode`.

## Artifact

`gopyt check` writes one `build/out.gobyte` for the whole package graph + stdlib (gitignored). Not per-module files.

```
magic     "GPYT"
version   u8 = 3
flags     u8 = 0
toolchain bytes[32] — exact runtime-source fingerprint
effects   — encoded per function, not globally
constn    u32
consts    Const[constn]
texprn    u32
texprs    TypeExpr[texprn]
typen     u32
types     Type[typen]
fnn       u32
fns       Func[fnn]
httpn     u32
routes    HttpRoute[httpn]
egressn   u32
egress    EgressOrigin[egressn]
evolven   u32
evolve    EvolvePolicy[evolven]
```

Endian: little. Integers two’s complement.

`EvolvePolicy` is `(module: u32 str-constant index, max: u32, timeout_ms: u32,
reservoir: u32)`. Entries are module-sorted and unique; bounds are in
`1..2147483647`. Versions 1 and 2 must be rebuilt under the
[exact toolchain compatibility amendment](toolchain-compatibility-amendment-2026-09-09.md).

### Const

```
tag u8
  1 i64     i64
  2 f64     ieee754
  3 str     u32 nbytes, utf-8 bytes
  4 bytes   u32 nbytes, raw
  5 bool    u8 0|1
  6 unit    (empty)
  7 none    (empty)
  8 type    u32 type_id   (* interned record/enum/union shape *)
  9 fn      u32 fn_id
 10 effect  u16 mask
```

No `any` const tag.

### Type

`TypeExpr` is a recursive, self-delimiting prefix encoding:

| tag | meaning | payload |
|-----|---------|---------|
| 1–9 | `bool i32 i64 u32 u64 f64 str bytes unit` | none |
| 10 | optional | `u32 element_texpr` |
| 11 | list | `u32 element_texpr` |
| 12 | map | `u32 key_texpr`, `u32 value_texpr` |
| 13 | nominal record/enum | `u32 type_id` |
| 14 | union | `u16 count`, then `u32 member_texpr[count]` |

The pool contains structurally unique entries in first-seen order during the
deterministic emit. Union members are flattened and de-duplicated in source order;
fewer than two members is invalid.

Type ids are assigned as specified in `implementer.md`. Each entry is:

```
kind      u8  1=record 2=enum 3=toolchain opaque
name      u32 const index (str; fully qualified)
count     u16 field count (record) or variant count (enum)
```

For an opaque type, `count` must be zero and only the toolchain may emit it. For a
record, `count` repetitions follow: `field_name u32`, `field_type u32`.
For an enum, each variant is `variant_name u32`, `field_count u16`, followed by
that many `field_name u32`, `field_type u32` pairs. `field_type` indexes `texprs`.
Malformed or out-of-range indices reject the artifact before execution.

### Func

```
name      const index (str)
kind      u8  1=fn 2=task 3=workflow 4=test 5=compiler parallel arm 6=stdlib native
arity     u16
nlocals   u16
effects   u16 bit mask (must be 0 if kind=fn)
param_types u32[arity] indexes into texprs
return_type u32 indexes into texprs
local_types u32[nlocals-arity] indexes into texprs
code_len  u32
code      bytes
```

For kind 6, `code_len` is zero and no code bytes follow. `name` is the fully
qualified callable name from `stdlib.md`; the VM dispatches by that exact name and
verifies its arity, parameter types, return type, and effects against the shipped
stdlib declaration before loading. No application or dependency may define kind
6. Unknown or mismatched native names are E100.

Effect bits (S9), low to high:

| bit | effect |
|-----|--------|
| 0 | network |
| 1 | filesystem.read |
| 2 | filesystem.write |
| 3 | database.read |
| 4 | database.write |
| 5 | time |
| 6 | random |
| 7 | log |
| 8 | model |
| 9 | ffi |
| 10 | secret |
| 11 | observe |

Unknown bits must be 0. App functions with bit 9 set are illegal (`GOPYT_E069`); only stdlib `store.db` may set `ffi`.

## Operand stack

Values are GC-tagged. `fn` frames cannot execute effect opcodes. Locals are slots
`0..nlocals-1`; arguments occupy `0..arity-1`. Every frame tracks initialized
slots. `STORE_LOCAL` to an initialized slot always traps 10 in every v0 build.

## Opcodes

One-byte opcode. Operands are little-endian. `u8`/`u16`/`u32`/`i32`. Jumps are byte offsets from the **next** instruction (signed i32).

| op | name | operands | stack | rule |
|----|------|----------|-------|------|
| 0x00 | NOP | | | |
| 0x01 | HALT | | | end of `gopyt run` entry |
| 0x02 | TRAP | u16 code | | contract/overflow/zero-div |
| 0x03 | POP | | x → | |
| 0x04 | DUP | | x → x x | |
| 0x10 | CONST | u32 const_ix | → c | |
| 0x11 | SOME | | x → some(x) | |
| 0x12 | UNIT | | → unit | |
| 0x20 | LOAD_LOCAL | u16 slot | → v | |
| 0x21 | STORE_LOCAL | u16 slot | v → | bind |
| 0x22 | RESET_LOCAL | u16 slot | → | end non-parameter activation |
| 0x30 | GET_FIELD | u16 field_ix | record-or-enum → v | payload fields use declaration order; trap if wrong type/tag |
| 0x31 | NEW_RECORD | u32 type_id, u16 n | v*n → rec | fields in decl order |
| 0x32 | NEW_ENUM | u32 type_id, u16 variant, u16 n | v*n → enum | |
| 0x33 | ENUM_TAG | | enum → i64 | variant index |
| 0x34 | VALUE_TYPE | | value → i64 | runtime type id (never traps) |
| 0x35 | IS_NONE | | optional → bool | true only for none |
| 0x36 | SOME_VALUE | | some(T) → T | compiler-generated after none check; bad tag traps 9 |
| 0x40 | NEW_LIST | u16 n | v*n → list | |
| 0x41 | LIST_LEN | | list → i64 | |
| 0x42 | LIST_GET | | list, i64 → v | trap if OOB / neg |
| 0x43 | LIST_APPEND | | list, v → list | new list |
| 0x44 | NEW_MAP | | → map | empty |
| 0x45 | MAP_GET | | map, k → v? | |
| 0x46 | MAP_SET | | map, k, v → map | new map |
| 0x50 | ADD_I64 | | a b → a+b | trap overflow |
| 0x51 | SUB_I64 | | a b → a-b | trap overflow |
| 0x52 | MUL_I64 | | a b → a*b | trap overflow |
| 0x53 | DIV_I64 | | a b → a/b | trap b==0 |
| 0x54 | MOD_I64 | | a b → a%b | trap b==0 |
| 0x55 | NEG_I64 | | a → -a | trap overflow (min i64) |
| 0x60 | EQ | | a b → bool | same type |
| 0x61 | NE | | a b → bool | |
| 0x62 | LT_I64 | | a b → bool | |
| 0x63 | LE_I64 | | a b → bool | |
| 0x64 | GT_I64 | | a b → bool | |
| 0x65 | GE_I64 | | a b → bool | |
| 0x66 | EQ_STR | | a b → bool | |
| 0x70 | NOT | | b → bool | |
| 0x71 | AND | | a b → bool | |
| 0x72 | OR | | a b → bool | |
| 0x80 | JUMP | i32 off | | |
| 0x81 | JUMP_IF_FALSE | i32 off | b → | |
| 0x82 | JUMP_IF_TRUE | i32 off | b → | |
| 0x90 | CALL_FN | u32 fn_id, u16 argc | args… → ret | callee effects must be 0 |
| 0x91 | CALL_TASK | u32 fn_id, u16 argc | args… → ret | current frame effects ⊇ callee |
| 0x92 | RETURN | | v → | pops frame |
| 0xA0 | REQUIRE | | bool → | false → TRAP req |
| 0xA1 | ENSURE | | bool → | false → TRAP ens |
| 0xB0 | PARALLEL | u16 n, u16 max, u32 timeout_ms, then n × (u32 fn_id) | capture*n → list | see below |

No opcode for arbitrary FFI/effects, exceptions, unwrap, goto-out-of-frame, or
spawn-without-wait. Host interaction exists only through verified kind-6 stdlib
functions; their declared effect masks are checked by `CALL_TASK`.
`CALL_TASK` and PARALLEL arm checks **mask out bit 9 (`ffi`)**: stdlib `ffi` does
not propagate (S9).

`VALUE_TYPE` is total (never trap 9). Nominal records/enums yield their interned
`type_id` (`0..n-1`). Scalars use reserved **negative** ids (not interned):

| id | kind |
|----|------|
| -1 | bool |
| -2 | i32 |
| -3 | i64 |
| -4 | u32 |
| -5 | u64 |
| -6 | str |
| -7 | bytes |
| -8 | unit |

`none` / `some` boxes are not union members; use `IS_NONE`. `f64` is not a legal
union member (S7).

## PARALLEL

`PARALLEL n max timeout_ms fn_id[n]`:

- `n >= 1`, `max >= 1`, `timeout_ms >= 1` or TRAP (S14, S20).
- Each `fn_id` is a kind-5 compiler-generated arm: arity 1 (its compiler-created
  capture record), same return type `T`, and effects ⊆ parent. Immediately before
  `PARALLEL`, the stack contains one capture record per arm in source order. Each
  record contains exactly the referenced outer locals, in ascending local-slot
  order. The instruction pops all captures before scheduling.
- Scheduler starts arms in source order and runs at most `min(n, max)` at once.
- If wall time exceeds `timeout_ms`, TRAP timeout (`time` effect required).
- Success: push `list[T]` in **source order**.
- Arm failure (TRAP) fails the parent. No leftover arms (D7).
- On first trap or timeout, no new arm starts. The VM requests cancellation of
  started arms, native calls observe it at their next boundary, and the parent
  waits for every started arm to terminate before trapping. Effects completed
  before cancellation are not rolled back. v0 provides no transactional promise.

## Traps

Traps are not result unions. They stop the task. Codes:

| code | meaning |
|------|---------|
| 1 | requires failed |
| 2 | ensures failed |
| 3 | i64 overflow |
| 4 | division by zero |
| 5 | index / length |
| 6 | parallel timeout |
| 7 | parallel max |
| 8 | missing effect |
| 9 | type/tag mismatch |
| 10 | double STORE_LOCAL |
| 11 | unknown opcode / const |
| 12 | call depth exceeded |
| 13 | `assert_eq` failed |
| 14 | allocation limit exceeded |

Business `NotFound` is a **value** (NEW_RECORD / NEW_ENUM), not a trap.

## Calls and effects

The checker sets `effects` on the Func. The VM:

- `CALL_FN` if callee.effects != 0 → trap 8
- `CALL_TASK` if callee.effects ⊈ caller.effects → trap 8

`fn` frames never contain `CALL_TASK` or `PARALLEL`.

## Validation before execution

The loader rejects bad magic/version/flags, truncated operands, invalid UTF-8
strings, invalid tags or ids, jumps outside a function or into an instruction,
stack underflow, inconsistent stack height/type at a control-flow join, invalid
function kind/effects, an invalid native stdlib entry, and a nonempty stack at
`HALT`. Rejection is
`GOPYT_E100 opcode`; malformed bytecode is never partially executed.

## HTTP route table

Each route is encoded as:

```
module_name  u32 const index (str)
method       u8  1=get 2=post 3=put 4=patch 5=delete
path         u32 const index (str)
handler      u32 fn_id
paramn       u16 handler arity
param_names  u32[paramn] const indices (str), handler declaration order
```

`param_names` carries the handler's parameter names because `implementer.md` 12
binds path placeholders and the JSON body **by name**; without them a standalone
artifact could not serve. `paramn` must equal the handler's arity, each name must
be a `snake` string constant, and every `{name}` in `path` must appear exactly
once among them; otherwise the artifact is rejected (E100).

Routes are sorted by module name, method number, then path bytes. Duplicate
method/path pairs in a module are `GOPYT_E091`. `net.http.serve` receives the
calling module id from the VM and exposes exactly that module's routes.

Each egress entry is `module_name u32` and `origin u32`, both string-constant
indices. Entries contain canonical origins defined by `security.md` and are
sorted by module then origin bytes. A kind-6 outbound native receives the caller
module id and rejects an origin absent from this table. Duplicate entries are
invalid bytecode (E100); source duplicates are E004.

## GC

Heap objects: str, bytes, list, map, record, enum, some-box, opaque Secret.
Immediate: bool, unit, none, i32/i64/u32/u64, f64.

Roots: stack, locals, scheduler arm stacks. Tracing mark-sweep. No opcode. FFI pointers are **not** roots unless wrapped by a handle record from `store.db` / `net.http`.

## Lowering sketch

| source | bytecode |
|--------|----------|
| `name = expr` | expr; STORE_LOCAL |
| `return e` | e; RETURN |
| `if c { a } else { b }` | c; JUMP_IF_FALSE else; a; JUMP end; else: b |
| enum `match` | ENUM_TAG; jump table; bind payload fields; arm expr |
| union `match` | VALUE_TYPE; jump table; bind record fields or take scalar arm |
| optional `match` | IS_NONE; branch; SOME_VALUE for the `some` arm |
| `p.amount` | LOAD p; GET_FIELD |
| `payments.stripe.charge(payment)` | LOAD payment; CALL_TASK |
| `requires e` | e; REQUIRE |
| `some(value)` | value; SOME |
| `none` | CONST none |
| `parallel max 8 timeout_ms 5000 { e1 e2 }` | PARALLEL 2,8,5000,arm1,arm2 |

## What is not in v0 bytecode

Register ISA, SSA, native JIT, WASM, exceptions, threads without PARALLEL, mutable in-place list ops (append returns a new list; opcode LIST_APPEND is semantically copy-plus-push, implementation may reuse storage if uniquely owned and unobservable).

[Finite binary64 amendment](finite-f64-amendment-2026-09-10.md) defines literal
rounding, overflow and nonfinite rejection at bytecode and VM boundaries.
