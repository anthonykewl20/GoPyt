# GoPyT v0 implementer rules

Peon local inference is narrowly extended by [the local runtime amendment](peon-runtime-amendment-2026-09-06.md).

Amended by [the implementation closure amendment](runtime-amendment-2026-09-05.md).

Must follow every locked doc. This file exists so an implementing agent **does not guess**. If a behavior is not here and not in the other locked files, it is illegal — do not invent it.

Host of the `gopyt` binary is unspecified (Python/Go/Rust/…). The **language** is still the bytecode VM.

---

## 1. Values and equality

- Arithmetic `+ - * / %` and `< > <= >=` : **`i64` only**.
- `i32` `u32` `u64` `f64` may be stored, passed, returned, JSON-converted (except `f64` JSON). They may not appear in `+ - * / %` or order compares. Convert via `core.int.*`.
- `==` / `!=` : `bool`, `i32` `i64` `u32` `u64`, `str`, `bytes`, `unit`, `none`/`some`, records, enums, lists, maps, unions (by active variant). **Not** `f64` (`GOPYT_E099`).
- Equality is structural. Map equality ignores key order.
- `unit` equals `unit`.
- Record equality: same type id, every field `==`.
- Enum equality: same type, same variant index, payload `==`.
- Structural equality is available only when every contained field/element/value
  type itself supports equality. A list/map/record/enum/optional/union containing
  `f64`, `Secret`, or another non-equality type is rejected by E099/E021 before
  execution. `core.test.assert_eq` applies the same recursive rule.
- `core.secret.Secret` is an opaque VM value. It cannot be constructed, inspected,
  compared, serialized, logged, or converted by application code. It can only be
  obtained from `core.secret.get`, passed/stored/returned, or explicitly
  declassified by `core.secret.reveal`.

`/` and `%` on `i64`: toward zero; remainder has the sign of the dividend (C/Go). `%` with `b == 0` or `/` with `b == 0` → trap 4.

## 2. `unit`, `return`, `if`

- The value is written `unit` (keyword, also a type). `return unit` is required for `-> unit`. Bare `return` is `GOPYT_E011`.
- `if` is a **statement only**. No if-expression. Use `match` for value-producing branches.
- A `Block` does not yield a value. No implicit/tail return.
- Call arguments evaluate left-to-right. After binding them, `requires` clauses
  run in declaration order before the body. On a normal `return`, `ensures`
  clauses run in declaration order with `result` bound, before control returns to
  the caller. The first false clause traps 1 or 2. Ensures do not run after a
  trap. Contracts are enabled in every v0 build and cannot be optimized away.

## 3. `open`

`open id` normally produces `GOPYT_E065 open_hole` and `gopyt check` fails. The
only closed exception is `open needs_test_<test_name>` on a public callable: it is
an acceptance-test obligation. It is discharged iff `test/<module>.gopyt`
contains exactly one `test <test_name>`. Missing or duplicate is E065. The test's
effects are inferred from its calls. Discharged obligations are not runtime
contracts. No warning severity.

## 4. Generics

- `TypeParams` / `TypeArgs` use **commas**: `map[K, V]`,
  `fn identity[T](value: T) -> T`.
- Call: `core.list.len(items)` infers `T`. If inference fails: `core.list.len[Payment](items)` — type args immediately before `(`.
- No higher-kinded types, no `where`, no variance.
- Generics are compile-time monomorphized. Every call has concrete inferred or
  explicit type arguments; unresolved variables are E029. Polymorphic recursion
  is E029. Emit one function record per distinct concrete type-argument vector,
  sorted by the canonical TypeExpr encoding. No type parameter appears in the
  artifact; kind-6 stdlib generics are instantiated to concrete signatures too.
- Trait dispatch is static. The concrete first argument selects the unique
  `(Trait, Type)` provide at check time and the call lowers directly to that
  provide member's function id. There are no trait objects or runtime method
  lookup in v0.

## 5. Map keys

`map[K, V]`: `K` is `str`, `i64`, or `bool` only. Other `K` → `GOPYT_E029`.

`core.map.keys` returns `list[K]` sorted: `bool` false then true; `i64` ascending; `str` byte-wise UTF-8 ascending.

## 6. Literals

- Integers: digits, no leading zero unless the number is `0`. Value must fit in `i64` or `GOPYT_E010`. Type `i64`.
- Floats: `digits . digits` only (`1.0` not `1.` or `.5`). Type `f64`. No `+`/`-` in the token; unary `-` is `NEG` only for i64 — **unary minus on f64 is illegal in v0** (`GOPYT_E021`).
- Strings: `"` … `"` ; escapes only `\\` `\"` `\n` `\t` `\r`. Other `\` → `GOPYT_E010`. Raw UTF-8 otherwise. No hex/unicode escapes.
- No character literals, no hex integers, no underscores in numbers.

## 7. Names

- `pascal_chunk` = one upper + at least one lower or digit (`Id`, `Http`, `User`).
  `A` and `HTTP` are illegal (`GOPYT_E016`). `T`, `K`, and `V` are the sole
  exceptions and only while bound as generic type variables.
- Max identifier length 64 bytes. Max `module_path` 8 segments. Max file size 1_048_576 bytes. Over → `GOPYT_E010`.
- A parameter/argument/effect/use/task/type-argument list has at most 65_535
  entries. `parallel` has 1..65_535 arms and `max` is 1..65_535;
  `timeout_ms` is 1..4_294_967_295. Violations use the construct's existing
  diagnostic (`E022`, `E054`, or `E029`).

## 8. Package layout (CLI)

- Package root = directory of `gopyt.toml`. CLI walks up from cwd until found, else `GOPYT_E046`.
- `gopyt.toml` `name` is the package name. It does **not** have to match a module path.
- `spec/` `impl/` `test/` are directly under the root. No other `.gopyt` roots.
- Paths in errors use `/` even on Windows.

## 9. CLI

| argv | behavior |
|------|----------|
| `gopyt check` | typecheck; if ok, emit `build/out.gobyte`; validate lock (E041 if stale, do not write lock) |
| `gopyt fmt` | rewrite every `.gopyt` in spec/impl/test in place to canonical form |
| `gopyt test` | `check` then run every `test` in `test/` |
| `gopyt run <module.path.task>` | `check` then call that **task** or **workflow**; **arity 0**. A `fn` is E074. |

No flags. Unknown subcommand → stderr one line + exit 1.

Exit: `0` ok, `1` check/fmt/test errors, `2` runtime trap.

Tests run by file path then test name, lexicographic UTF-8. Effects are inferred
exactly. Tests run sequentially in one process. `store.db` persists across test
runs and process restarts at the package root; tests must explicitly reset their
fixture state or use fresh package roots and distinct keys. First trap fails the run (exit
2). `assert_eq` mismatch is trap 13. There is no effect interception runtime.

`gopyt run` prints nothing for `unit`. Other return values: `data.json.encode` to stdout plus newline, or `GOPYT_E097` if not `Json`. Traps do not print the value.

## 10. Bytecode artifact

One file: `build/out.gobyte` (entire package graph + stdlib). Not per-module files.

Type ids: monomorphize every reachable record/enum instantiation. Sort concrete
record, enum, and toolchain-opaque types by fully qualified name bytes followed
by canonical type-argument encodings, then assign `0..n-1`. Uninstantiated
generic declarations have no runtime type id.

Fn ids: sort all concrete fn/task/workflow/test/provide-member/compiler-arm and
stdlib-native instantiations by fully qualified compiler symbol bytes followed by
their canonical concrete type-argument encodings. User declarations cannot
overload or reuse a
callable name within a module. Internal provide symbols are
`provide:<Trait>:<Type>:<member>` and parallel-arm symbols are
`arm:<owner>:<zero_based_source_index>` using the declarations' exact UTF-8
spellings; colons are impossible in source identifiers, so the encoding is
unambiguous. These names are artifact-internal. Assign ids `0..n-1`.

Emit type definitions first in type-id order, then callables in function-id order.
Traverse fields, parameters, and union members in declaration/source order.
Type-expression and constant pools use first-seen order during that traversal.

## 11. Stack / limits (traps)

| trap | when |
|------|------|
| 12 | call depth > 256 |
| 13 | `assert_eq` fail |
| 14 | allocation would exceed 2_147_483_647 elements or bytes |

Keep traps 1–11 as in bytecode.md.

## 12. HTTP server (`task serve`)

Amended by [HTTP input deadlines and framing](http-input-amendment-2026-09-05.md):
absolute input deadlines, truncated-body rejection, and TCP_NODELAY.

- Bind `127.0.0.1:8080` unless env `GOPYT_HTTP_ADDR` is `host:port` (no other forms).
- Match method + path. `{name}` is one segment (`[^/]+`).
- Route paths are ASCII, begin with `/`, contain no empty/`.`/`..` segment,
  query, fragment, percent escape, or trailing slash (except path `/`). A
  placeholder occupies a whole segment and its name occurs once. Two routes in
  one module whose same-method patterns can match the same path are E091; there
  is no precedence rule.
- Handler parameters are in declaration order but are bound by name. For `get`
  and `delete`, parameters must be exactly the path placeholders. For `post`,
  `put`, and `patch`, they are the placeholders plus zero or one non-path JSON
  body parameter. Query parameters do not exist in v0. Handler return is `unit`
  or must implement `Json`; otherwise E097.
- No route → response status 404, body empty.
- Trap in handler → body empty: worker-admission trap 7 returns 503, nested timeout trap 6 returns 504 while output time remains, and other traps return 500. Expiration of the whole ten-second request budget (queue, input, cooperative execution and output) closes the connection; see the parallel-admission amendment.
- Handler success: status **200**, body = UTF-8 JSON of the **return value** (`Json`). If return is `unit`, body empty.
- **No status-code mapping from `NotFound` etc.** Clients decode the JSON union/record.
- `get`: no request body. Extra body ignored (not an error).
- `post`/`put`/`patch`: body is JSON for the **single non-path parameter** (must `Json`). If the task has only path params, body must be empty.
- `delete`: like `get`.
- When a JSON body is required, a present `Content-Type` must be exactly
  `application/json`; a missing header is accepted. Wrong content type or decode
  failure means the handler is not called and the server returns 400 with an
  empty body.
- One outstanding `serve` per process. Second `serve` → `ListenError`.
- The server runs at most 64 handler children concurrently under the `serve`
  task, with a FIFO queue of 1024 accepted requests. When full it returns 503 with
  an empty body. Request bodies over 1_048_576 bytes return 413 empty. SIGINT or
  SIGTERM stops accepting, drains active responses within existing deadlines,
  and joins handler children before `serve` returns `unit`, as defined by the
  parallel-admission amendment. Explicit context cancellation or deadline expiry
  aborts connections and joins children before propagating the terminal condition.
  Bind/listen failure returns `ListenError`; it is not a trap.

## 13. HTTP client (`net.http.request`)

Uses the host network. The parsed URL origin must exactly match a normalized
origin in the calling module's `egress { }`, as defined by `security.md`, else
`HttpError` (E111 for a statically known mismatch). Request URLs with userinfo are
illegal. No redirects. Timeout is
30_000 ms; it belongs to `network`, not the `time` effect. TLS uses hostname
verification and the host trust store. A response body over 8_388_608 bytes,
DNS/TLS/socket failure, or timeout returns `HttpError`; any received HTTP status
100..599 is a successful `HttpResponse` value.

## 14. Files, DB, model, time, random, log

- `core.file` paths use `/` and contain one or more nonempty UTF-8 segments. Empty,
  `.` or `..` segments, backslash, leading/trailing slash, NUL, and every symlink
  component are rejected with `IoError`. Resolution stays beneath the package
  root; `write` may create the final regular file but not parent directories.
- `store.db`: durable package-local SQLite snapshots; keys as given. Individual
  operations are serialized across cooperating processes, and `compare_exchange`
  performs conditional insertion/replacement atomically. See the normative
  [storage amendment](storage-amendment-2026-09-05.md) for limits, durability,
  errors and ephemeral storage when an embedder omits the VM root. The
  [cache amendment](storage-cache-amendment-2026-09-05.md) specifies bounded
  validated read-result reuse and cross-process invalidation.
- `core.model.complete`: if `GOPYT_MODEL_URL` is absent/invalid/unlisted, return
  `ModelError`. Otherwise POST canonical JSON `{"prompt": ...}` with content type
  `application/json`; require HTTP 2xx and an exact JSON object containing only
  string field `text`. Network, status, or decode failure is `ModelError`. No
  vendor SDK or retry exists.
- `core.time.now_ms`: Unix epoch milliseconds (UTC).
- `core.random.i64_in`: host CSPRNG, uniform inclusive. Not seedable in v0.
- `core.log.write`: UTF-8 line to stderr. Argument type `str` only, never `Secret`.
- `core.secret.get`: `name` must match source `snake`; otherwise return
  `NotFound`. Read env `GOPYT_SECRET_<NAME>` with ASCII uppercase. Absent means
  `NotFound`; present value is preserved exactly in the opaque `Secret`. No secret
  files exist in the repo.

## 15. Stdlib linkage

Stdlib modules are **not** files in the app. The compiler provides them. `use core.list { append }` is legal without `[deps]`.

## 16. `http` effects on `serve`

Checker treats handler tasks as called from `serve`. Effects of those handlers count as used on `serve`. `network` is used by `net.http.serve`.

## 17. What you must not add

Macros, annotations, `async`, methods, `pub`, version ranges, a second JSON
encoding, SQL, extra CLI flags, extra opcodes, extra stdlib names, `any`, `_`
patterns, if-expressions, implicit returns, platform `int`, effect interception,
app `ffi`, `eval`/`exec`/`shell`, or open outbound HTTP without `egress`.

### Peon host development integration

[Delegation v2](peon-delegation-v2.md) adds host developer tooling around the existing
GoPyT compiler and VM. It adds no language syntax, native signatures, bytecode
version or GoPyT CLI commands. Model output remains subject to explicit grants
and real checks; local execution is not an adversarial OS sandbox.

[Finite binary64 amendment](finite-f64-amendment-2026-09-10.md) defines literal
rounding, overflow and nonfinite rejection at bytecode and VM boundaries.

[Exact monetary values](money-amendment-2026-09-10.md) defines checked fixed-point
arithmetic, explicit rounding, currency validation and decimal text boundaries.

[Typed time amendment](time-amendment-2026-09-10.md) defines nanosecond timestamps,
durations, explicit-offset serialization, clock origins and anomaly behavior.

[Parallel admission and deadlines](parallel-admission-amendment-2026-09-10.md)
adds a shared VM worker bound, inherited deadlines and explicit overload behavior;
blocking-I/O and graceful-drain qualification under #13 remains open.
