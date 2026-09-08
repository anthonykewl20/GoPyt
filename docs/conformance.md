# GoPyT v0 conformance snippets

A compiler is not done until these hold. Do not “fix” snippets to a dialect.

Package for each snippet: `gopyt.toml` with `name = "test_package"` and
`version = "0.1.0"` plus the files shown, and the canonical `gopyt.lock` for
those bytes (a missing lock is `GOPYT_E041`, whose repair is that text —
`docs/lockfile.md`). A harness writes the lock from that repair before judging
the snippet's own expectation.

## Must accept (after impl filled)

### C001 — pure i64

`spec/math.gopyt`

```
module math

fn add(left: i64, right: i64) -> i64
    requires true
    ensures result == left + right
```

`impl/math.gopyt`

```
module math

fn add(left: i64, right: i64) -> i64
    requires true
    ensures result == left + right
{
    return left + right
}
```

`gopyt check` exit 0.

### C002 — missing use

`spec/orders.gopyt`

```
module orders

task ping() -> i64
    effects { log }
```

`impl/orders.gopyt` body calls `core.log.write("x")` without `use` → `GOPYT_E013`. Repair:

```
use core.log { write }
```

Call site stays `core.log.write("x")`.

### C003 — inexhaustive match

Matching `User | NotFound` with only `User` arm → `GOPYT_E070`.

### C004 — fn cannot call task

`fn` body calling `core.log.write` → `GOPYT_E053`.

### C005 — int not a type

`value: int` → `GOPYT_E020`. Legal: `i64`.

### C006 — UserID illegal

`type UserID { }` → `GOPYT_E016`. Legal: `UserId`.

### C007 — leading zero

`01` → `GOPYT_E010`.

### C008 — if is not an expression

```
number = if true { 1 } else { 0 }
```

→ `GOPYT_E011`. Use `match`.

### C009 — open fails check

`requires open needs_gateway` → `GOPYT_E065`.

### C010 — parallel requires bounds

`parallel { 1 2 }` → `GOPYT_E054`. Legal: `parallel max 2 timeout_ms 1000 { 1 2 }`.

### C011 — Json required

`data.json.encode(payment)` without `provide Json for Payment` → `GOPYT_E097`.

### C012 — serve arity

`gopyt run billing.api.serve` is the HTTP entry (`-> unit | ListenError`, arity 0). `gopyt run billing.api.post_charge` → `GOPYT_E022` if arity ≠ 0.

## Must not exist

These tokens/forms are always errors: `class` `async` `int` `string` `any` `null` `_` `=>` `&&` `pub` `from x import` `user.name()` as a call, f-strings, version `^1.0.0`.

## Additional release assertions

| ID | Input or condition | Required result |
|----|--------------------|-----------------|
| C013 | `fn pair(left: i64 right: i64)` | E011; comma required |
| C014 | `fn pair(left: i64, right: i64)` | parses |
| C015 | `effects { network database.write }` | E011; comma required |
| C016 | `effects { network, database.write, }` | E011; no trailing comma |
| C017 | body for `-> unit` falls through | E073 |
| C018 | `return unit` in a `-> unit` body | accepts |
| C019 | `core.list.empty[Payment]()` in scope | type is `list[Payment]` |
| C020 | `core.map.empty[str, Payment]()` in scope | type is `map[str, Payment]` |
| C021 | `map[bytes, str]` | E029; key type is closed |
| C022 | `parallel max 2 timeout_ms 1000 { 1 2 3 }` | accepts; at most two arms run |
| C023 | `parallel max 0 timeout_ms 1000 { 1 }` | E054 |
| C024 | `gopyt run math.boom` on an arity-0 **task** that does `1 / zero` | E101 with `trap: 4` |
| C025 | `if true { return 1 }` in value position | E071 |
| C026 | same input formatted twice | byte-identical after first format |
| C027 | malformed bytecode jump into an operand | E100 before execution |
| C028 | unknown effect `http` | E052 |
| C029 | non-`needs_test_` open contract | E065 |
| C030 | stale lockfile | E041 containing exactly one full replacement lock |
| C031 | `test` calls stdlib `store.db.put` with `{ database.write, ffi }` | accepts; test stores `database.write`; stdlib `ffi` does not propagate (S9) |
| C032 | an effectful test run | uses specified runtime service; no effect interception |
| C033 | `open needs_test_charge_zero` with one matching task test | obligation discharged |
| C034 | app `effects { ffi }` | E069 |
| C035 | `net.http.request` with no `egress` | E111 |
| C036 | `core.log.write(secret_value)` where value is `Secret` | E112 |
| C037 | JSON object with extra key vs record | `ConvertError` |
| C038 | call to undeclared `eval` | E074 or E110 |
| C039 | `evolve` without `model` | E114 |
| C040 | evolve candidate fails check | E115; old digest remains |
| C041 | `evolve { max 0 timeout_ms 1 reservoir 1 }` | E117 |
