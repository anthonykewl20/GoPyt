# GoPyT v0 stdlib

Peon local inference is narrowly extended by [the local runtime amendment](peon-runtime-amendment-2026-09-06.md).

Must follow: `docs/spec.md` S9/S21/S23/S24, `docs/grammar.ebnf`.

This is the **complete** user-callable stdlib. It is declaration-only GoPyT, shipped by the toolchain, not copied into app `spec/`. Unknown `use` of anything else is an error.

No synonyms (`len` vs `length`, `put` vs `set` vs `insert`). Apps cannot `provide` for builtin types (orphan rule); only this package may.

Prefixes: `core`, `net`, `data`, `store` (lockfile.md).

---

```
module core.status

type NotFound {
}

type ConvertError {
    message: str
}

type IoError {
    message: str
}

type DbError {
    message: str
}

type ModelError {
    message: str
}

type ListenError {
    message: str
}

type HttpError {
    message: str
}

type TestFailed {
    message: str
}

type Throttled {
}
```

---

```
module core.list

fn empty[T]() -> list[T]

fn len[T](items: list[T]) -> i64
    ensures result >= 0

fn get[T](items: list[T], index: i64) -> T?
    requires index >= 0

fn append[T](items: list[T], item: T) -> list[T]

fn range(start: i64, end: i64) -> list[i64]
    requires start <= end
```

`range` is end-exclusive. `range(0, 0)` is empty. `get` out of bounds or `index >= len` → `none` (not a trap). Negative index fails `requires` (trap).

---

```
module core.map

fn empty[K, V]() -> map[K, V]

fn len[K, V](map_value: map[K, V]) -> i64
    ensures result >= 0

fn get[K, V](map_value: map[K, V], key: K) -> V?

fn set[K, V](map_value: map[K, V], key: K, value: V) -> map[K, V]

fn keys[K, V](map_value: map[K, V]) -> list[K]
```

`set` returns a new map. Missing key → `none`, not `NotFound`. `K` is `str`, `i64`, or `bool` only (`docs/implementer.md`). `keys` is sorted.

---

```
module core.str

use core.status { ConvertError }

fn len(text: str) -> i64
    ensures result >= 0

fn concat(left: str, right: str) -> str

fn from_i64(value: i64) -> str

fn slice(text: str, start: i64, end: i64) -> str | ConvertError
    requires start >= 0
    requires end >= start
```

No `eq` (use `==`). No interpolation. `slice` end-exclusive; if `end > len(text)` → `ConvertError`.
`core.str.len` and `slice` count Unicode scalar values, not UTF-8 bytes or
grapheme clusters. Strings are not Unicode-normalized; equality compares the
exact scalar sequence. `core.bytes.len` counts bytes.

```
module core.bytes

use core.status { ConvertError }

fn len(data: bytes) -> i64
    ensures result >= 0

fn from_str(text: str) -> bytes

fn to_str(data: bytes) -> str | ConvertError

fn concat(left: bytes, right: bytes) -> bytes
```

`from_str` is UTF-8 bytes (always succeeds). `to_str` fails `ConvertError` if not UTF-8.

---

```
module core.int

use core.status { ConvertError }

fn to_i64_from_i32(value: i32) -> i64
fn to_i64_from_u32(value: u32) -> i64
fn to_i64_from_u64(value: u64) -> i64 | ConvertError
fn to_i32(value: i64) -> i32 | ConvertError
fn to_u32(value: i64) -> u32 | ConvertError
fn to_u64(value: i64) -> u64 | ConvertError
```

No `f64` conversions in v0. Names are explicit so agents do not invent `as i32`.

---

```
module core.convert

use core.status { ConvertError }

trait FromStr {
    fn from_str(text: str) -> Self | ConvertError
}

trait Json {
    fn to_json(value: Self) -> str | ConvertError
    fn from_json(text: str) -> Self | ConvertError
}

provide FromStr for i64
provide FromStr for bool
provide FromStr for str

provide Json for bool
provide Json for i32
provide Json for i64
provide Json for u32
provide Json for u64
provide Json for str
provide Json for unit
```

`FromStr` parsing consumes the entire input. `i64` accepts `0` or optional `-`
followed by ASCII digits with no leading zero and returns `ConvertError` on range
overflow. `bool` accepts only `true` or `false`. `str` returns its input unchanged.
No surrounding whitespace is ignored.

`f64` is **not** `Json` or `FromStr` in v0 (S20). `bytes` is not `Json` (use base64 later; do not invent it now).

`T?`, `list[T]`, and `map[str, V]` are `Json` when their contained value types
are `Json`. A named union is `Json` when every member is `Json`. The compiler
derives these compositions; no user `provide` is written for them.

App records/enums: spec must `provide Json for Type`. E-D fills the body (one encoding, below). No custom JSON in v0.

Canonical JSON (determined):

| GoPyT | JSON |
|-------|------|
| `bool` `i64` `str` | JSON bool / number / string |
| `T?` | value or `null` (only JSON null in the whole language; source still has no `null`) |
| `list[T]` | array |
| `map[str, V]` | object |
| record | object, keys = field names, decl order; **exact** key set |
| enum no payload | `{"Pending": {}}` |
| enum with payload | `{"Paid": {"amount": 1}}` |
| union | `{"module.Type[args]": <member JSON>}` using the active member's fully qualified canonical type name (omit `[args]` when absent) |
| `unit` | `{}` |

No second encoding. Record and enum keys use declaration order; `map[str, V]`
keys use UTF-8 byte order. Strings use RFC 8259 escaping with non-control UTF-8
left unescaped. Decode rejects duplicate object keys, unknown record keys,
missing non-optional fields, a union object with other than one known member key,
and trailing non-whitespace input. `i32`/`u32`/`u64` JSON numbers must be integral
and in range; exponent notation is accepted only when its mathematical value is
an in-range integer. Otherwise decode returns `ConvertError`. `f64`, `bytes`, and
`Secret` are not `Json`, including transitively inside a provided record/enum.

HTTP `{id}` params: type `str` as-is; otherwise the type must `provide FromStr`.

---

```
module core.test

fn assert_eq[T](left: T, right: T) -> unit
```

Mismatch → trap 13 (`TestFailed`), not a result union. Illegal for `f64`. Legal
in `test` blocks and in `fn` bodies. Test effects are inferred (S28).

---

```
module core.log

task write(message: str) -> unit
    effects { log }
```

```
module core.time

use core.status { ConvertError }

type Timestamp {
    unix_ns: i64
}

type Duration {
    ns: i64
}

type MonotonicInstant {
    ticks_ns: i64
    clock_id: str
}

fn timestamp_ns(value: i64) -> Timestamp | ConvertError
fn timestamp_ms(value: i64) -> Timestamp | ConvertError
fn duration_ns(value: i64) -> Duration | ConvertError
fn duration_ms(value: i64) -> Duration | ConvertError
fn timestamp_to_ms(value: Timestamp) -> i64 | ConvertError
fn duration_to_ms(value: Duration) -> i64 | ConvertError
fn add(value: Timestamp, delta: Duration) -> Timestamp | ConvertError
fn difference(later: Timestamp, earlier: Timestamp) -> Duration | ConvertError
fn duration_add(left: Duration, right: Duration) -> Duration | ConvertError
fn elapsed(later: MonotonicInstant, earlier: MonotonicInstant) -> Duration | ConvertError
fn parse_timestamp(text: str) -> Timestamp | ConvertError
fn format_timestamp(value: Timestamp) -> str | ConvertError
fn parse_duration(text: str) -> Duration | ConvertError
fn format_duration(value: Duration) -> str | ConvertError

task now() -> Timestamp | ConvertError
    effects { time }

task monotonic_now() -> MonotonicInstant | ConvertError
    effects { time }

task sleep(duration: Duration) -> unit | ConvertError
    effects { time }

task now_ms() -> i64
    effects { time }

task sleep_ms(ms: i64) -> unit
    effects { time }
    requires ms >= 0
```

Sleep supports the full nonnegative i64 range using monotonic elapsed time and
observes inherited cancellation between bounded host waits. See the
[native boundary amendment](native-boundary-amendment-2026-09-09.md).

```
module core.random

task i64_in(min: i64, max: i64) -> i64
    effects { random }
    requires min <= max
    ensures result >= min
    ensures result <= max
```

Inclusive max.

```
module core.file

use core.status { IoError, NotFound }

task read(path: str) -> bytes | NotFound | IoError
    effects { filesystem.read }

task write(path: str, data: bytes) -> unit | IoError
    effects { filesystem.write }
```

```
module core.model

use core.status { ModelError }

task complete(prompt: str) -> str | ModelError
    effects { network, model }

task local(prompt: str, max_tokens: i64) -> str | ModelError
    effects { model }
    requires max_tokens >= 1 and max_tokens <= 64
```

No streaming, no tool-calling API here. Agent tools are app `task`s. Caller module
needs `egress` containing `GOPYT_MODEL_URL`'s exact normalized origin. Remote
model requests follow the [URL and JSON payload limits](parallel-admission-amendment-2026-09-10.md#outbound-request-preparation-bounds).

---

```
module core.secret

use core.status { NotFound }
use core.str { len }

task get(name: str) -> Secret | NotFound
    effects { secret }
    requires core.str.len(name) > 0

task reveal(value: Secret) -> str
    effects { secret }
```

`Secret` is an **opaque** stdlib type (not a `type Secret { }` the app can write). Only `get` produces it. `Secret { }` in user code is `GOPYT_E021`. See `docs/security.md`.

---

```
module core.observe

use core.str { len }

type Report {
    events: i64
    fail: i64
    mean_ms: i64
    var_ms: i64
    cusum_alarm: bool
}

task report() -> Report
    effects { observe }

task note(tag: str) -> unit
    effects { observe }
    requires core.str.len(tag) > 0
```

See `docs/hardening.md`. VM also records traps/HTTP without `note`.

---

```
module core.limit

use core.status { Throttled }

task allow(key: str, tokens: i64, refill_ms: i64) -> unit | Throttled
    effects { time }
    requires tokens > 0
    requires refill_ms > 0
```

Token bucket (RFC 2697/2698). One limiter API.

---

```
module core.evolve

type NoChange {
}

type EvolveError {
    message: str
}

type Applied {
    digest: str
}

task propose() -> Applied | NoChange | EvolveError
    effects { model, time, log, observe, filesystem.read, filesystem.write }
```

Legal only with `evolve { }` on an agent (`docs/evolve.md`). Compiler fills the body.

---

```
module net.http

use core.status { HttpError, ListenError }
use core.str { len }

enum HttpMethod {
    Get
    Post
    Put
    Patch
    Delete
}

type HttpRequest {
    method: HttpMethod
    url: str
    body: bytes
}

type HttpResponse {
    status: i64
    body: bytes
}

task request(req: HttpRequest) -> HttpResponse | HttpError
    effects { network }
    requires core.str.len(req.url) > 0

task serve() -> unit | ListenError
    effects { network }
```

`net.http.serve` may **only** be called from a module-local `task serve` in a module that has `http { }`. Routes come from that table. The compiler binds them; there is no handler list value.

`net.http.request` requires the calling module's `egress { }` (S30). Its parsed
origin must match an entry exactly. Outbound requests follow the
[URL and payload limits](parallel-admission-amendment-2026-09-10.md#outbound-request-preparation-bounds).

`HttpResponse.status` produced by stdlib is an `i64` in `100` … `599`.

---

```
module data.json

use core.status { ConvertError }

fn encode[T](value: T) -> str | ConvertError

fn decode[T](text: str) -> T | ConvertError
```

Requires `Json` for `T`. No `encode_pretty`.

---

```
module store.db

use core.convert { Json }
use core.status { DbError, NotFound }
use core.str { len }

task get(key: str) -> str | NotFound | DbError
    effects { database.read, ffi }
    requires core.str.len(key) > 0

task put(key: str, value: str) -> unit | DbError
    effects { database.write, ffi }
    requires core.str.len(key) > 0

task compare_exchange(key: str, expected: str?, value: str) -> bool | DbError
    effects { database.read, database.write, ffi }
    requires core.str.len(key) > 0


type Change {
    key: str
    expected: str?
    value: str?
}

type Snapshot {
    values: list[str?]
}

provide Json for Change

provide Json for Snapshot

task get_many(keys: list[str]) -> Snapshot | DbError
    effects { database.read, ffi }

task compare_exchange_many(changes: list[Change]) -> bool | DbError
    effects { database.read, database.write, ffi }
```

String KV only. No SQL string in v0 (agents hallucinate dialects). `ffi` is listed because the host DB is foreign; it does **not** propagate. Callers declare `database.read` / `database.write` only (S9).

Storage is durable per package at `.gopyt-state/store.sqlite3`. Each successful
write survives a VM or process restart. `compare_exchange` atomically inserts
when `expected` is `none`, or replaces an existing value equal to `some(text)`.
It returns `true` only when that write commits; an absent key or unequal value
returns `false` without a write. `get` and `put` remain separate operations;
use `compare_exchange` to reject a stale update. Errors return `DbError`.

`get_many` returns an ordered consistent snapshot. `compare_exchange_many`
atomically checks and applies a bounded set of `Change` records, including
deletion using `value: none`. Every condition must match or no key changes.
See the [batch amendment](batch-storage-amendment-2026-09-09.md) for bounds,
ambiguous failure outcomes, read conditions and operator namespace authority.

The implementation serializes cooperating processes and bounds the database
snapshot to 64 MiB. Read misses load the snapshot; successful writes replace the
whole snapshot. A bounded cache reuses validated results while the safely opened
file's fingerprint remains unchanged under the database lock. This implementation
favors confinement and atomicity over large-database throughput. See the
[storage amendment](storage-amendment-2026-09-05.md) for durability, limits, reset
and embedding semantics, and the [cache amendment](storage-cache-amendment-2026-09-05.md)
for freshness rules and retained-payload limits.

---

## Call form

Always qualified:

```
core.list.append(items, item)
core.str.concat(left, right)
Named.name(user)
Store.get(db, key)
net.http.request(req)
```

`use core.list { append, len }` still requires `core.list.append(...)` at the call site (S4).

### Peon host development integration

[Delegation v2](peon-delegation-v2.md) adds host developer tooling around the existing
GoPyT compiler and VM. It adds no language syntax, native signatures, bytecode
version or GoPyT CLI commands. Model output remains subject to explicit grants
and real checks; local execution is not an adversarial OS sandbox.

## Exact monetary values

See [the fixed-point amendment](money-amendment-2026-09-10.md) for bounds, rounding
and validation. Currency codes are caller-assigned; no exchange-rate or currency
registry policy is inferred.

```
module core.money

use core.status { ConvertError }

type Money {
    units: i64
    scale: i64
    currency: str
}

enum Rounding {
    Exact
    HalfEven
    HalfAway
    TowardZero
    Floor
    Ceiling
}

fn make(units: i64, scale: i64, currency: str) -> Money | ConvertError

fn parse(text: str, scale: i64, currency: str, rounding: Rounding) -> Money | ConvertError

fn format(value: Money) -> str | ConvertError

fn add(left: Money, right: Money) -> Money | ConvertError

fn subtract(left: Money, right: Money) -> Money | ConvertError

fn compare(left: Money, right: Money) -> i64 | ConvertError

fn rescale(value: Money, scale: i64, rounding: Rounding) -> Money | ConvertError

fn multiply_ratio(value: Money, numerator: i64, denominator: i64, rounding: Rounding) -> Money | ConvertError
```

Ordinary `data.json.encode` and generated `Json.to_json` obey the existing
2,147,483,647-byte allocation ceiling for canonical escaped UTF-8 output;
exceeding it traps 14 before returning a partial string. This is a per-value
limit, separate from the smaller HTTP request/response budgets and aggregate
process memory. See [implementer allocation rules](implementer.md#11-stack--limits-traps).
