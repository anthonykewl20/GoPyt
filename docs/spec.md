# GoPyT Language Spec (v0)

Peon local inference is narrowly extended by [the local runtime amendment](peon-runtime-amendment-2026-09-06.md).

Amended by [the implementation closure amendment](runtime-amendment-2026-09-05.md).

Status: locked (S1–S32 + companions)  
Must follow: `docs/charter.md` (D1–D13). If a spec choice would break the charter, the charter wins or must be amended first.

This document is the v0 language contract: spec format, syntax, types, effects, modules, VM. It is filled one locked decision at a time. It is not the compiler.

Forward-looking phrases retained inside early S-sections record the order in which
decisions were made; they are historical, not open requirements. S27 and the
current companion documents contain the completed rule set.

Standing rule for closed vocabulary (names, builtins, effects, keywords): pick the **single** agent-checkable form. No synonyms. Wrong form is a compile error. Guessing is not allowed. Agents, not humans, will emit most of this; the checker is the anti-hallucination layer.

| ID | Topic |
|----|--------|
| S1–S4 | spec files, layout, modules, `use` |
| S5–S8 | names, builtins, optionals/errors, no holes |
| S9–S11 | effects, keywords, contracts |
| S12–S15 | agents/APIs, declarations, control flow, visibility |
| S16–S21 | bindings, tests, CLI, impl/git, diagnostics, numeric traps |
| S22 | grammar.ebnf, bytecode.md, interview-tree.md |
| S23–S26 | traits/provide, elaborator, lockfile, task traits |
| S27 | precedence and closed-world implementation |
| S28 | compiler-first validation and effect-honest tests |
| S29 | constrained AI emission surface |
| S30 | language-enforced security (`docs/security.md`) |
| S31 | self-evolution (`docs/evolve.md`) |
| S32 | observe / analyze / harden (`docs/hardening.md`) |

---

## Locked spec decisions

### S1 — Intent spec on disk

**Choice: A. Declaration-only GoPyT.**

The intent spec is written in GoPyT, not in YAML, JSON, or Markdown.

- Spec files use the same grammar as implementation.
- Spec files contain declarations only: modules, types, `fn`/`task` signatures, agents, effects, invariants, tests/examples.
- Spec files contain **no function bodies** and no algorithms.
- Implementation files are generated/elaborated and contain bodies.
- One language, two roles: **contract** vs **implementation**.

Example as originally discussed (the current syntax is frozen below):

```
task charge(payment: Payment) -> Receipt | Declined
    effects { network, database.write }
    requires p.amount > 0
```

What this decision rejects:

- A separate YAML/JSON spec language (two type systems).
- Markdown-with-fences as the compiler input.
- Mixed contract+body in one file as the default (layout is S2).

Implications (not yet designed, only constrained):

- The parser must accept body-less declarations as a legal spec form.
- The checker must reject bodies in spec files and reject missing contracts where D11 requires them.
- Chat/elicitation writes these files; the compiler never compiles a prompt (D11).
- File names, folders, and extensions are the next decision.

### S2 — Canonical project layout

**Choice: A. `spec/` + `impl/` + manifest.**

v0 tree:

```
app/
  gopyt.toml          # package name, version, dependencies
  spec/               # source of truth; humans/agents edit
    orders.gopyt
    billing.gopyt
  impl/               # generated bodies; not for daily review
    orders.gopyt
    billing.gopyt
  test/
```

- Extension is `.gopyt` in both `spec/` and `impl/`.
- Role is the directory, not the extension.
- `spec/` is declaration-only (S1). `impl/` contains elaborated bodies.
- `impl/` is compiler-owned. Editing it by hand is not the workflow; the checker may warn or refuse drift against `spec/`.
- `gopyt.toml` is the package manifest; the completed lock format is in `lockfile.md`.

What this decision rejects:

- Spec-only on disk with implementation only in cache.
- Flat `*.spec.gopyt` next to `*.gopyt`.
- One folder per module (`orders/contract.gopyt`) as the v0 layout.

Implications (superseded where later S-numbers speak):

- Git tracks `spec/` always. `impl/` is committed (S17). `build/` is not.
- Canonical formatter applies to both trees.
- Tests live in `test/` (S17, S22).

### S3 — File ↔ module mapping

**Choice: A. Path is the module name; one file = one module.**

- `spec/orders.gopyt` is module `orders`.
- `spec/payments/stripe.gopyt` is module `payments.stripe`.
- Nested folders are `.`-separated module paths. Files are `snake` or a later frozen identifier rule; the mapping itself is path → module id.
- A `module …` header is allowed and **must equal** the path-derived name. Mismatch is a compile error.
- `impl/` mirrors `spec/` one-to-one: `spec/orders.gopyt` ↔ `impl/orders.gopyt`.
- No extra files in `impl/` that lack a spec twin. No two files for one module.

What this decision rejects:

- Module name taken only from a header while the path is free.
- Go-style “directory = package, many files.”
- One namespace for the whole app.

Implications (not yet designed, only constrained):

- Canonical layout includes canonical file names, not only folders.
- AI cannot split a module across `utils.gopyt` and `orders.gopyt` for taste.
- Identifier spelling is completed by S5.

### S4 — Module header and imports

**Choice: A, tightened for AI agents.** Required `module` header and `use`, with one canonical reference form per symbol kind and no wildcards.

An agent must be able to answer three questions without guessing:

1. **Is this file a module?** Yes iff it starts with `module <name>` and that name matches the path (S3).
2. **Is an import missing?** Yes iff a name from another module is used and there is no `use` for that module (or the name is not in that `use` allowlist).
3. **How do I import it?** There is only one legal form.

Canonical shape:

```
module orders

use payments.stripe { Payment, charge }
use auth { UserId }

task place(id: UserId) -> Order
    effects { database.write }
```

Rules:

- `module <name>` is required and is the first declaration in the file.
- Every other module you mention needs a `use <module.path> { Name, name, ... }`.
- The `{ ... }` list is the allowlist. Using an unlisted name is a missing-import error.
- Imported PascalCase types are referenced by their allowlisted short name
  (`Payment`, `UserId`); foreign callable values remain module-qualified
  (`payments.stripe.charge(...)`). Enum variants are `Type.Variant`; trait calls
  are `[module.]Trait.member(...)`. These are kind-specific forms, not aliases.
  Two foreign types with the same short name therefore cannot both be imported in
  one module; split the module or amend a public name. No `from` or `as` exists.
- No `use *`, no string paths, no implicit imports, no import-from-parent magic.
- Unused `use` lines and unused allowlist names are errors (keeps the spec honest).
- The compiler’s repair is a single line: the exact `use … { … }` to add or the exact name to put in the list.

What this decision rejects:

- Header-optional files (agent cannot see the module id in the first line).
- String imports and wildcard imports.
- Multiple import dialects (`from`, `import`, `as`, or callable short aliases).

Implications (not yet designed, only constrained):

- Diagnostics must be structured and stable so an agent can apply the repair without reading the whole repo (charter D1, D2).
- `impl/` files use the same `module` / `use` rules as `spec/`.
- Identifier spelling (types vs functions vs files) is the next decision.

### S5 — Naming style

**Choice: A, tightened to stop slop.** Types are PascalCase. Everything else is snake_case. Kind is visible from the name. The compiler rejects every other spelling.

This is not taste. It is so an agent can tell **what a name is** without guessing, and cannot emit a second legal spelling for the same thing.

| Kind | Form | Examples | Illegal |
|------|------|----------|---------|
| Module / file path | dotted snake_case | `payments.stripe`, `spec/payments/stripe.gopyt` | `Payments`, `payments-stripe`, `stripe.Gopyt` |
| Type, enum, variant, trait | PascalCase | `Payment`, `NotFound`, `UserId` | `payment`, `user_id`, `UserID` |
| `fn`, `task`, field, local | snake_case | `charge`, `amount`, `user_id` | `Charge`, `getUser`, `user-id` |
| Effect | dotted snake_case | `database.write`, `network` | `DatabaseWrite`, `db_write` as a synonym of `database.write` |

Anti-slop rules:

- **One spelling per identifier. No aliases.** `UserId` is the only form. Not `UserID`, `USER_ID`, `userId`.
- **Acronyms are words, not shouts:** `Http`, `Id`, `Url`, `Api`, `Json`. Never `HTTP`, `ID`, `URL`, `API`. Agents mix those constantly; the checker rejects the shout form.
- **No kebab-case** in identifiers or file names.
- **No leading/trailing underscore**, no `__dunder__` names in user code.
- **No one-letter names** except generic type variables `T`, `K`, and `V`, legal
  only inside the declaration that binds them. Value names remain at least two
  characters. Other one-letter type variables are illegal.
- Types and values must not collide by case-folding: you cannot have `type Order` and `task order` in the same module. Different kinds still need different words (`place_order` vs `Order`).

What this decision rejects:

- Go-style PascalCase functions (`Charge`) — type and task look the same.
- All-snake types (`type payment`) — type and value look the same.
- Dual conventions or “formatter will fix it” as the only gate. Invalid names are **compile errors**, not warnings.

Implications (not yet designed, only constrained):

- Canonical formatter emits this style; the checker enforces it even if the formatter is skipped.
- Stdlib names must obey the same table so agents do not learn a second dialect.
- Built-in type spellings (`int` vs `i64`, `str` vs `string`) are the next decision — they must also have exactly one name each.

### S6 — Built-in types

**Choice: A, frozen.** One name, one width, no synonyms. If the width is not in the name, the type does not exist.

| Name | Meaning | Not these |
|------|---------|-----------|
| `bool` | `true` / `false` only | `True`, `Boolean`, `bit` |
| `i32` `i64` `u32` `u64` | integers, width in the name | `int`, `number`, `usize`, `nat` |
| `f64` | binary float | `float`, `f32`, `number`, `double` |
| `str` | UTF-8 text | `string`, `String`, `text`, `char`, `rune` |
| `bytes` | raw bytes | `byte[]`, `u8[]` as a synonym, `Buffer` |
| `list[T]` | sequence | `List`, `Array`, `Vec`, `[]T` |
| `map[K, V]` | map | `dict`, `Dict`, `HashMap`, `object` |
| `T?` | optional (see S7) | `Optional[T]`, `Maybe`, `null` |
| `unit` | no value | `void`, `()`, `None`, `nil` |

Anti-slop rules:

- User code cannot declare a type named like a builtin.
- Unsuffixed integer literals are **i64**. Unsuffixed float literals are **f64**. That default is frozen in the spec, not inferred from the host machine.
- No platform `int`. No `usize` in v0 (lengths are `i64`; negative length is a check error).
- No `any`, `unknown`, `object`, `dynamic`, `never` as a user escape. (S8)
- No `char`. One character is still `str` (or later a stdlib type, not a second builtin).

### S7 — Optional and errors

**Choice: one optional form; errors are named unions; no exceptions; no unwrap bang.**

Optional:

- Written only as `T?`.
- Values: `some(value)` or `none`.
- Using a `T?` as a `T` is a compile error. No implicit unwrap, no `email!`, no null deref.

Errors / results:

- A task returns a union of named types: `User | NotFound | DatabaseError`.
- Every union has two or more distinct members. A member is a nominal record,
  enum, or opaque type, **or** a scalar builtin (`bool`, `i32`, `i64`, `u32`,
  `u64`, `str`, `bytes`, `unit`). That matches stdlib `str | ConvertError` and
  `unit | Throttled`. Optionals, lists, maps, type variables, nested unions, and
  `f64` are illegal members. A member value injects into an expected union
  without a conversion; a nominal member's runtime type is the active member.
- Each failure is a PascalCase type, not a string, not a generic `Error`, not `Result[T, E]`.
- No exceptions, no `throw`/`try`/`catch` in v0.
- `match` on a union is exhaustive. Adding `Unauthorized` to a public return type is a breaking change (charter D5).
- `T?` is not a stand-in for errors. Absence and failure are different.

```
task load_user(id: UserId) -> User | NotFound | DatabaseError
    effects { database.read }
```

What this rejects: `Optional[T]`, `Result[T, E]`, exceptions, `null`, unwrap operators.

### S8 — No holes, no silent conversions

**Choice: if the checker cannot see the type, the program is illegal.**

- No `any` / `object` / untyped collections.
- No implicit conversions: `"5" + 3` is illegal; `i32` is not `i64`; `str` is not `bytes`.
- Conversions are named and usually fallible: they return `T | ConvertError` (or `T?` only when the only failure is absence).
- No string interpolation in v0 (agents invent types inside f-strings). Format through an explicit stdlib `fn`.
- String literals are `"..."` only. No `'c'` character literals, no triple-quote dialect in v0 (one more syntax later if needed, not two now).
- `true` / `false` lowercase only.

### S9 — Effect catalog

**Choice: a closed list. No synonyms. HTTP is `network`, not a second effect.**

v0 effects (complete list):

| Effect | Means |
|--------|--------|
| `network` | sockets, HTTP, other hosts |
| `filesystem.read` | read local files |
| `filesystem.write` | create/write/delete local files |
| `database.read` | read a database |
| `database.write` | write a database |
| `time` | clock, sleep, timeouts as time source |
| `random` | randomness |
| `log` | logging |
| `model` | LLM / model calls |
| `ffi` | foreign call; **stdlib only** (S30) |
| `secret` | read/reveal `Secret` values |
| `observe` | local sketches + reservoir (S32); not stderr |

Rules:

- `fn` has the empty effect set. It cannot call a `task`.
- A declared `task`, `workflow`, or trait `task` has at least one effect. If the
  exact set is empty, the declaration is a `fn`; `effects { }` is E050.
- Public `task` signatures list **all** effects. Missing an effect is a compile error; listing an unused effect is a compile error.
- No `http` + `network`. No `db` + `database`. No `io`. No `pure`. No `tool` effect: a tool is a `task` whose effects propagate.
- Unknown effect names are errors (agents cannot invent `analytics` unless we add it to this table).
- App modules cannot list `ffi` (`GOPYT_E069`). Host DBs use `ffi` only inside
  `store.db`. **`ffi` on a stdlib native does not propagate:** callers list
  `database.read` / `database.write` only. Otherwise no app could call `store.db`.
- Which host a `network` task may call is `egress { }` (S30), not a free-form capability system.

### S10 — Core keywords (v0 closed set)

**Choice: one word per idea. No class/struct/record/async/try.**

Legal keywords:

| Keyword | Role |
|---------|------|
| `module` | file identity (S4) |
| `use` | import (S4) |
| `type` | data type |
| `enum` | closed variants |
| `fn` | pure function |
| `task` | effectful function |
| `trait` | shared behavior (signatures only in spec) |
| `agent` | agent declaration |
| `workflow` | named multi-step process |
| `match` | exhaustive branch |
| `if` | boolean branch |
| `for` | bounded loop |
| `parallel` | structured concurrency (D7) |
| `return` | return |
| `effects` | effect list on a task |
| `requires` `ensures` `open` | spec contracts; `open` is an explicit hole |
| `some` `none` | optional values (S7) |
| `true` `false` | bool |
| `test` | test declaration |
| `http` `tasks` `provide` `egress` `evolve` | HTTP; agent; trait; URL allowlist; self-evolution |
| `unresolved` | impl hole (check fails until filled) |
| `else` `in` | `if` / `for` |
| `and` `or` `not` | bool ops (never `&&` `\|\|` `!`) |
| `max` `timeout_ms` | required `parallel` bounds |
| `Self` | trait/type only |
| `get` `post` `put` `patch` `delete` | reserved; HTTP verbs |
| builtins | `bool` `i32` `i64` `u32` `u64` `f64` `str` `bytes` `list` `map` `unit` |

Illegal in v0 (agents will try them; checker rejects):  
`class`, `struct`, `record`, `object`, `interface`, `abstract`, `async`, `await`, `go`, `throw`, `try`, `catch`, `except`, `null`, `nil`, `var`, `let`, `const`, `public`, `private`, `this`, `self` as a required receiver keyword, `while` (use bounded `for` or recursion), `import`, `from`, `eval`, `exec`, `system`, `popen`, `unsafe`, `shell`.

Loop rule: `for` must be over a collection or a bounded range. Unbounded loops are not a v0 construct.

### S11 — Contracts (`requires` / `ensures`)

**Choice: contracts are boolean GoPyT, or they are explicitly `open`. Never comments.**

```
task charge(payment: Payment) -> Receipt | Declined
    effects { network, database.write }
    requires payment.amount > 0
    ensures match result {
        Receipt { amount } -> amount == payment.amount
        Declined -> true
    }
```

Rules:

- `requires` / `ensures` are expressions of type `bool`.
- They may use arguments, fields, literals, type-valid comparisons, `and` `or`
  `not`, `match`, `some`/`none`, and calls to `fn` only. Ordering is i64-only;
  structural equality follows S20.
- They may not call `task`, do I/O, or embed English.
- `ensures` may mention `result` (the value returned).
- If the compiler cannot check a rule, it is not a comment. It must be:

  `open needs_gateway_idempotency`

  with a stable id. `open` means the context is not closed (charter RP-001). Agents cannot hide holes as prose.
- `open needs_test_<test_name>` is allowed **only on a public `fn`**. It is a
  traceable acceptance-test obligation, discharged by exactly one `test <test_name>`
  (elaborator.md). It is illegal on `task`/`workflow`. All other `open` ids
  fail `gopyt check` with `GOPYT_E065`.
- Failed `requires` at runtime is a contract violation, not a returned business error. Business failures stay in the result union (S7).

What this rejects: markdown contracts, stringly `require("amount positive")`, unchecked comments, mixing `Declined` with thrown exceptions.

### S12 — Agents, workflows, HTTP

**Choice: they are modules of tasks, not a second language and not decorators.**

**Agent** — a named bundle with a **max effect set** and an explicit task list. Tasks are siblings in the same module, not nested classes.

```
module billing.agent

agent BillingAgent
    effects { network, database.write, model }
    tasks { collect, refund }

task collect(id: InvoiceId) -> Receipt | Declined
    effects { network, database.write, model }

task refund(id: InvoiceId) -> unit | NotFound
    effects { database.write }
```

- Agent names are PascalCase (type-like).
- An agent lists at least one sibling task. Its effect set is exactly the union of
  the listed task effects; extra effects violate least privilege (E095). Extra,
  duplicate, or missing task names are E094.
- Optional `evolve { max N timeout_ms M reservoir K }` (S31). Requires `model`.
- No `class Agent`, no `@tool`, no Python decorators.

**Workflow** — a `task` tagged as orchestration. Same signature, effects, and
`gopyt run` rules as `task`. v0 does **not** enforce “wiring only” (no extra
diagnostic): a `workflow` body is checked like a `task`. Prefer `task` for
domain algorithms; the tag is for agents, not a second type system.

```
workflow bill_cycle(id: InvoiceId) -> Receipt | Declined
    effects { network, database.write }
```

**HTTP** — not annotations. One block, same module as the handler tasks:

```
module billing.api

http {
    post "/charge" post_charge
    get "/invoices/{id}" get_invoice
}

task post_charge(payment: Payment) -> Receipt | Declined
    effects { network, database.write }
```

- Verbs allowed: `get` `post` `put` `patch` `delete` only.
- Path is a string. `{name}` binds a `str` argument of the same name, or a type
  with a `fn from_str(text: str) -> T | ConvertError`.
- Handler must be a `task` in this module. No middleware syntax, no `@Get`, no `router.get`.
- An `http` block has at least one route. Duplicate or overlapping method/path
  patterns are E091.
- Outbound HTTP/model in this module requires `egress { }` (S30).

### S13 — Declaration syntax

**Choice: one shape per construct. Fields one per line. No commas in type bodies. Delimited inline lists use commas.**

```
type Payment {
    amount: i64
    currency: str
}

enum Status {
    Pending
    Paid {
        amount: i64
    }
    Failed {
        reason: str
    }
}

fn normalize(text: str) -> str

task charge(payment: Payment) -> Receipt | Declined
    effects { network, database.write }
    requires p.amount > 0
```

Impl adds a body **after** the header. `{` on its own line:

```
fn normalize(text: str) -> str
{
    return text
}
```

Record construction with multiple fields is:

```
Payment {
    amount: 1
    currency: "USD"
}
```

Even a marker record uses braces (`NotFound {}`). A payload-free enum value is
`Status.Pending` without braces. A payload variant is
`Status.Paid { amount: 1 }`.

- No `struct`/`class`/`record`. No positional tuples in v0 (a tuple is just a `type` with fields).
- `:` always name then type: `amount: i64`. Never Go’s `amount i64`.
- `->` for return types only. Never `=>`.
- `trait` is signatures only in v0. No default method bodies.

```
trait Named {
    fn name(value: Self) -> str
}
```

- Generics use `[]`, never `<>`: `type Box[T] { value: T }`, `list[str]`.
- Inline lists (parameters, arguments, `use`, effects, agent tasks, type parameters,
  and type arguments) use `, ` with no trailing comma. Newline-delimited fields,
  variants, statements, patterns, and match arms never use commas.
- **No methods.** Calls are always `module.path.fn(args)`, `module.path.task(args)`, or `Named.name(value)` (S23). Field read is `p.amount` only.
- `Self` is legal only inside `trait` and `provide` bodies.

### S14 — Control flow

**Choice: `match` is the workhorse. `if`/`for`/`parallel` have one form each.**

```
match status {
    Status.Pending -> 0
    Status.Paid { amount } -> amount
    Status.Failed { reason } -> 0
}

if ready {
    return some(value)
} else {
    return none
}

for item in items {
    audit.record(item)
}

results = parallel max 8 timeout_ms 5000 {
    payments.stripe.charge(p1)
    payments.stripe.charge(p2)
}
```

Rules:

- `match` accepts only an enum, a union of named types, or `T?`; it is exhaustive
  over variants, member types, or `some`/`none`. It does not match scalar
  literals. `_` is illegal in v0 because it would hide a new variant/member.
- `->` in match arms. No `=>`.
- No `elif`. Use `else { if ... }` or `match`.
- No ternary.
- `for` iterates a `list[T]` or `fn range(start: i64, end: i64) -> list[i64]`
  (end exclusive). It is a statement and produces no collection or loop-carried
  value. Pure accumulation uses recursion; effectful iteration calls tasks.
  Unbounded `while` is illegal (S10).
- `parallel` **requires integer literals** for `max` (> 0) and `timeout_ms` (> 0).
  Missing bounds are errors. `max` is the concurrency ceiling and may be smaller
  than the number of arms. A timeout uses the `time` effect.
- All `parallel` arms have the **same type** `T`. Result is `list[T]`. No fire-and-forget, no unnamed stray tasks.
- `if` is a statement only, never an expression. `else` may be omitted. Value-producing branches use `match`.
- The `unit` value is written `unit`. Bare `return` is illegal; use `return unit`.

### S15 — Visibility

**Choice: no `pub`/`private` keywords. Location is visibility.**

- Anything declared in `spec/` is public contract.
- Anything declared only in `impl/` is module-private.
- A public signature may only mention public types.
- `impl/` must mirror `spec/` headers 1:1 (S3). Extra public-looking tasks in impl without a spec twin are errors.
- Agents cannot “forget `pub`” because the keyword does not exist.

### S16 — Bindings, mutation, calls

**Choice: names do not change. That stops a large class of agent bugs.**

- `name = expr` binds once. Rebinding the same name in the same lexical scope is a
  compile error. A name bound inside an `if`, `match`, `for`, or `parallel` arm is
  not visible outside it. No `let`/`var`/`mut`/`const`.
- No `+=` as mutation of a name. Build new values (`list.append` returns a list).
- No methods, no UFCS, no implicit `self`.
- Calls: `payments.stripe.charge(payment)` only. Missing `use` is S4.
- Field access: `p.amount`. Nested: `p.customer.id`.
- Comparison: `==` `!=` `<` `>` `<=` `>=` only. No `===`.
- Boolean: `and` `or` `not` words, not `&&` `||` `!` (those collide with unwrap bang, which is illegal).
- Every reachable path returns explicitly, including `return unit` for return type
  `unit`. Falling off a body is `GOPYT_E073 return_path`; there are no implicit or
  tail returns.
- Operators evaluate left operand before right operand. Call arguments evaluate
  left-to-right. `and` and `or` short-circuit.

### S17 — Tests, comments, files on disk

**Tests** live in `test/`, one file per module: `test/orders.gopyt` is module `test.orders` and tests public `orders`.

```
test charge_rejects_zero
{
    ...
}
```

- A `test` block may call public `fn`, `task`, `workflow`, and trait members. Its
  effects are inferred as the exact union of calls and stored on the test bytecode
  function. Tests are not public contracts, so they have no source effect clause.
- A test's implicit signature is `() -> unit`, but its return is not implicit: all
  successful paths end with `return unit`.
- No `assert` keyword. Stdlib: `fn assert_eq[T](left: T, right: T) -> unit` (mismatch is trap 13).
- Static properties are validated by **`gopyt check`** before tests. Behavioral
  properties, including effectful workflows, belong in `gopyt test` (S28).

**Comments:** `//` to end of line only. No `/* */`. Comments are never contracts (S11).

**Files:** UTF-8, `\n` newlines, indent **4 spaces**, tabs are errors.

**`impl/` is committed to Git.** `gopyt check` fails if spec headers and impl headers drift. `build/` (bytecode cache) is **not** committed.

### S18 — CLI

**Choice: four commands. No aliases.**

| Command | Does |
|---------|------|
| `gopyt check` | parse, type/effect/contract check, spec↔impl headers |
| `gopyt fmt` | rewrite to the only canonical layout/syntax |
| `gopyt test` | `check` then run `test/` |
| `gopyt run <module.task>` | `check` then run that **task** or **workflow**; arity 0. A `fn` is E074. |

No `gopyt compile`, `gopyt exec`, `gopyt lint` as extra names. `fmt` is not optional in CI: unformatted code is a check failure.

### S19 — Diagnostics (agent repair)

**Choice: every error has a stable code and at most one canonical repair.**

Example:

```
GOPYT_E013 missing_use
file: spec/orders.gopyt
line: 12
repair-bytes: 40

use payments.stripe { Payment, charge }
```

- Machine-readable (structured) plus a short text line.
- No “maybe you meant” lists of 12 styles. One repair, or `open` if the checker cannot know.
- Codes are stable (`GOPYT_E013`). Agents key on the code, not the English.
- Full table: `docs/diagnostics.md`.

### S20 — Numbers and traps

**Choice: no wraparound, no platform size, no silent NaN policy later.**

- Unsuffixed integer → `i64`. Unsuffixed float → `f64` (S6).
- `i64` overflow, division by zero, `timeout_ms <= 0`, `parallel max <= 0` are **contract violations**, not wrap, not `Inf`.
- Lengths are `i64`. Negative length is a contract violation.
- No `NaN`-producing ops in v0 integer code; `f64` equality with `NaN` is not used in contracts (`requires` cannot use `f64` in v0 — too many assumptions). **v0 contracts are `bool`/`i64`/`str`/`enum`/`T?` only.**
- Arithmetic and ordering operators are available only for `i64` operands. `f64`,
  `i32`, `u32`, and `u64` are storage/boundary types in v0. Construct `f64` with
  a float literal and integer widths through `core.int`; compare integer widths
  only with `==`/`!=`. None of these four types supports arithmetic or ordering.
  Integer division truncates toward zero and
  remainder has the dividend's sign.
- Equality is structural for bool, integers, str, bytes, optionals, records, enums,
  lists, and maps. It is illegal for `f64`, functions, tasks, workflows, agents,
  and foreign handles. Map equality is independent of insertion order.

### S21 — v0 stdlib (closed)

Signatures: `docs/stdlib.md`. Agents may `use` only these modules:

| Module | Role |
|--------|------|
| `core.status` | `NotFound`, `ConvertError`, `IoError`, `DbError`, `ModelError`, `ListenError`, `HttpError`, `TestFailed`, `Throttled` |
| `core.list` | `empty`, `len`, `get`, `append`, `range` |
| `core.map` | `empty`, `len`, `get`, `set`, `keys` |
| `core.int` | `to_i32` `to_i64` `to_u32` `to_u64` (fallible width) |
| `core.str` | `len`, `concat`, `from_i64`, `slice` |
| `core.bytes` | `len`, `from_str`, `to_str`, `concat` |
| `core.convert` | traits `FromStr`, `Json`; provide for builtins |
| `core.test` | `assert_eq` |
| `core.log` | `write` (`log`) |
| `core.time` | `now_ms`, `sleep_ms` |
| `core.random` | `i64_in` |
| `core.file` | `read`, `write` |
| `core.model` | `complete` |
| `core.secret` | opaque `Secret`, `get`, `reveal` (`secret`) |
| `core.observe` | `report`, `note` (`observe`) |
| `core.limit` | token bucket `allow` |
| `core.evolve` | `propose` |
| `net.http` | `HttpMethod`, `request`, `serve` |
| `data.json` | `encode`, `decode` |
| `store.db` | string KV `get`/`put` |

Unknown `use` is an error. No `utils`, no `eq` on strings (use `==`), no SQL.

### S22 — Grammar, bytecode, interview

Locked in companion files (do not fork syntax there):

| File | Role |
|------|------|
| `docs/grammar.ebnf` | tokens + syntax |
| `docs/bytecode.md` | `.gobyte` + opcodes + traps |
| `docs/interview-tree.md` | elicitation state machine that writes `spec/` |
| `docs/elaborator.md` | spec → impl/test |
| `docs/lockfile.md` | `gopyt.toml` + `gopyt.lock` |
| `docs/security.md` | compiler/VM security |
| `docs/evolve.md` | self-evolution |
| `docs/hardening.md` | observe / CUSUM / harden |
| `docs/stdlib.md` | closed stdlib signatures |
| `docs/diagnostics.md` | `GOPYT_E*` catalog |
| `docs/implementer.md` | no-guess runtime/CLI |
| `docs/fmt.md` | formatter |
| `docs/conformance.md` | snippets |
| `docs/validation.md` | proven vs prototype |

Test files: `test/orders.gopyt` is module `test.orders` (`module test.orders` + `use orders { ... }`). Path is relative to `test/`, then prefixed with `test.`.

`gopyt.toml` keys: `name`, `version`, optional `[deps]` — see `docs/lockfile.md`.

### S23 — Traits and `provide`

**Choice: traits are contracts; `provide` is the only impl form; calls are `Trait.fn(value)`, never `value.fn()`.**

Spec (no body):

```
trait Named {
    fn name(value: Self) -> str
}

provide Named for User
```

Impl (body required):

```
provide Named for User
{
    fn name(value: Self) -> str
    {
        return value.display_name
    }
}
```

Rules:

- `provide Trait for Type` — `Type` must be declared in **this** module (orphan rule). No `provide Named for i64` in app code.
- One provide per `(Trait, Type)` in the whole package graph.
- Provide block contains the trait’s members: `fn` and/or `task` (S26).
- `Self` is `Type`. Signatures must match the trait after substituting `Self`.
- Call: `Named.name(user)` or `core.convert.FromStr.from_str(text)` if foreign. Not `user.name()`, not `impl`, not `implements`, not `instance`.
- No default method bodies. Two traits may both have `name`; call sites use `Named.name` vs `Label.name`.
- Kind from spelling: `Named.name` is `Pascal.snake` (call). `Status.Paid` is `Pascal.Pascal` (enum).
- Only the stdlib may `provide` for builtin types (`provide FromStr for i64`).

### S24 — Elaborator

Locked in `docs/elaborator.md`.

Interview writes `spec/`. Compiler E-D writes impl **headers** + `unresolved id` holes (and determined bodies only when unique, e.g. `serve`). Agent E-A replaces `unresolved` only. `gopyt check` fails while any `unresolved` remains.

`unresolved` is illegal in `spec/`.

HTTP: the first `http` route implies spec `task serve() -> unit | ListenError` with effects = `{ network }` ∪ handler effects. E-D fills the body with `return net.http.serve()`.

### S25 — Lockfile

Locked in `docs/lockfile.md`.

- Manifest: `gopyt.toml` — `name`, `version`, optional path `[deps]`
- Lock: `gopyt.lock` — `toolchain` + `[[pkg]]` name/version/path/digest
- No registry, no version ranges, no git deps in v0
- `gopyt check` does not rewrite the lock; stale lock → one repair (full lock text)

### S26 — Task traits

**Choice: a trait may contain `task` members. Effects on the trait are the caller contract. Provide effects must be a subset.**

```
trait Gateway {
    task charge(gateway: Self, payment: Payment) -> Receipt | Declined
        effects { network }
}

provide Gateway for StripeClient
```

Rules:

- Trait member is `fn` or `task`, not `workflow`.
- If any member is a `task`, the trait is effectful. A `fn` cannot call `Gateway.charge`.
- Call: `Gateway.charge(client, payment)` — still not
  `client.charge(payment)`.
- Caller must hold the **trait** effect set (here `network`), even if the provide body uses fewer.
- Provide `task` effects ⊆ trait effects. Provide cannot add `ffi` if the trait omitted it; widen the trait (spec change) instead.
- Provide `fn` members stay pure.
- `store.db` stays module tasks, not a required trait. Apps may wrap it in their own trait.

### S27 — Normative precedence and closed-world failure

The v0 completion audit corrected contradictions in the earlier locked draft. The
current text of the normative files is the contract; the research notebook is
historical. If normative documents appear to disagree, use this precedence:

1. `charter.md` for product constraints;
2. `spec.md` and `implementer.md` for language meaning;
3. `grammar.ebnf` for accepted source syntax;
4. each companion document for its named interface;
5. examples, which test the rules but never override them.

An implementation must not guess at unspecified behavior. It reports
`GOPYT_E001 internal` and stops until the contract is amended. Amendments must
change every affected normative document and the conformance fixtures together.

### S28 — Compiler-first validation and effect-honest tests

**Choice: AI runs static validation before behavioral tests; tests never erase effects.**

The agent loop is:

1. `gopyt fmt`
2. `gopyt check` — every problem is a `GOPYT_E*` with one repair (`docs/diagnostics.md`)
3. only then `gopyt test` or `gopyt run`

What the compiler must catch **before any test runs**: wrong types, missing/unused effects, inexhaustive `match`, spec/impl drift, missing `use`, `unresolved`, illegal names, `open` holes, `fn` calling a `task`, missing returns, contract expr errors.

Tests may call effectful code, and those effects remain visible in bytecode and
test reports. v0 has no effect-interception framework. A test may use an ordinary
implementation selected by the spec, but cannot dynamically substitute a
provider or claim that an intercepted effect proves production behavior.
`store.db` is the specified in-process v0 database, not a SQL double.

### S29 — Easy for agents, hard to slop

**Choice: shrink what an agent is allowed to emit until most mistakes are compile errors.**

Normative one-pager: `docs/ai-surface.md`.

- **Illegal** forms never run (closed names, effects, exhaustive match, and no
  hidden effect interception).
- **Determined** forms are written by fmt/elaborator, not by taste.
- **Open logic** is only `unresolved` bodies that still match the spec header.

The language is easy because there is **one** legal program shape. An agent that copies Python/Go/TS idioms fails `gopyt check` instead of shipping runtime errors. Logic that typechecks but is the wrong business rule is fixed by tightening `requires`/`ensures` on the spec, not by more syntax.

### S30 — Language-enforced security

**Choice: agents cannot skip security by forgetting a library. The checker and VM are the mechanism.**

Normative: `docs/security.md`.

- Default-deny effects; app code cannot use `ffi`.
- No eval/shell/SQL.
- Closed JSON (exact fields).
- Sandboxed files; outbound HTTP only via `egress { }`.
- `Secret` cannot be logged or JSON-encoded.
- Lockfile digest for what actually shipped.

This does not prove authorization business rules. It does block the usual AI-generated attack surface (injection, SSRF, secret-in-logs, dynamic code).

### S31 — Native self-evolution

**Choice: checked package versions from real traces, never live `eval`.**

`docs/evolve.md`. Hermes-style loop; gates are `gopyt check` + S30. `evolve { max N timeout_ms M reservoir K }` on an agent that has `model`.

### S32 — Self-analyze and self-harden

**Choice: bounded streaming math on real events; harden only via checked diffs.**

`docs/hardening.md`. Tools: `core.observe`, `core.limit`. Algorithms: Welford, Count-Min, Bloom, Vitter reservoir, Page CUSUM (`p0=0.02,k=0.01,h=8`), token bucket, Hedge on **checked** candidates. Compiler instruments traps/HTTP. Model is not called per request. Proof: `prototypes/observe/` (P0).

---

## v0 docs closed

Language surface is locked. Implementation may begin against `grammar.ebnf`, `stdlib.md`, `bytecode.md`, and `diagnostics.md`. Adding a name, keyword, effect, opcode, or `GOPYT_E*` code is an amendment to those files plus this spec.

Do not add keywords, types, effects, or opcodes without amending these docs.

### Peon host development integration

[Delegation v2](peon-delegation-v2.md) adds host developer tooling around the existing
GoPyT compiler and VM. It adds no language syntax, native signatures, bytecode
version or GoPyT CLI commands. Model output remains subject to explicit grants
and real checks; local execution is not an adversarial OS sandbox.
