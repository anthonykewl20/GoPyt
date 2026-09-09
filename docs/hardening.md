# GoPyT v0 self-analyze and self-harden

Amended by [the implementation closure amendment](runtime-amendment-2026-09-05.md).

Status: normative. Companion to `docs/evolve.md` and `docs/security.md`.

An app (e.g. authentication) that keeps running must **collect bounded real telemetry**, **analyze it with published streaming algorithms**, and **harden only through checked spec/impl changes**. It must not train a model on every request, store unbounded logs, or `eval` a patch into the live VM.

## Proven pieces (v0 uses these, not alternatives)

| Role | Result / algorithm | Why it is cheap |
|------|--------------------|-----------------|
| Monitor contracts at runtime | Runtime verification of `requires`/`ensures` as trace predicates (Havelund & Roşu, monitoring-oriented RV) | One boolean per contract already evaluated for traps |
| Static least privilege | Effects + `egress` (Saltzer & Schroeder 1975: least privilege, complete mediation — every HTTP call is a declared handler) | Compile time |
| Secret non-leak | `Secret` unusable as `str`/JSON/log (Goguen–Meseguer 1982 noninterference as a *type* cut, not a full lattice) | Compile time |
| Latency / size moments | Welford (1962) online mean and variance | O(1) space per counter |
| Path / error frequencies | Count-Min Sketch (Cormode & Muthukrishnan 2005) | O((1/ε) log(1/δ)) cells; freeze ε=0.01, δ=0.01 |
| Repeat-key without storing raw ids | Bloom filter (Bloom 1970) on **hashes** of rate-limit keys | Fixed bit array |
| Bounded sad-path examples | Reservoir sampling, Algorithm R (Vitter 1985) | Exactly `reservoir` traces |
| When to evolve (not every request) | Page CUSUM (1954) on a Bernoulli failure stream. v0 parameters `p0=0.02`, `k=0.01`, `h=8`, calibrated by `prototypes/observe/`. (Wald SPRT is the same sequential-testing family; not a second detector in v0.) | O(1) per event |
| Pick a checked candidate | Hedge / multiplicative weights (Littlestone–Warmuth 1994; Freund–Schapire 1997) | Regret bound vs best checked variant; no extra model calls at request time |
| Rate-limit after signal | Token bucket (Tanenbaum; RFC 2697/2698) | O(1) per allow |
| Bound in-flight hardening | Little’s law (Little 1961): at most one `propose`, ≤ `max` candidates | Scheduler already structured |

Compiler abstract interpretation (Cousot & Cousot 1977) stays on `gopyt check`. Runtime does **not** run a theorem prover per request.

Amended by [observation admission and terminal telemetry](parallel-admission-amendment-2026-09-10.md#observation-admission-and-terminal-telemetry): cancelled or expired contexts may omit terminal telemetry; sketches are not lossless audit logs.

## Effect `observe`

Local sketches + reservoir only. No stderr, no network. Bit 11.

The VM **always** records, without agent-written logs:

- contract trap code (1, 2, …)
- HTTP route + outcome tag (ok / ConvertError / 404 / 500 / handler union name)
- `egress` deny
- `GOPYT_E112` attempts
- task name

Payloads are **not** stored except the reservoir’s compact records (task, tag, i64 codes). `Secret` never enters a trace.

## Stdlib tools (the data plane)

```
module core.observe

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

`note` is optional extra. Auth sad paths should still be **types** (`Denied`, `Throttled`); the VM already counts those tags.

```
module core.limit

task allow(key: str, tokens: i64, refill_ms: i64) -> unit | Throttled
    effects { time }
    requires tokens > 0
    requires refill_ms > 0
```

```
module core.status
type Throttled { }
```

Token bucket is the only limiter. No second `rate_limit` API.

## Duty cycle (not resource hungry)

Per process:

- Sketches update **inline** on each already-required trap/HTTP edge (no extra model).
- `propose` runs only if `cusum_alarm` is true **and** no propose is in flight **and** `evolve { }` exists.
- Cooldown: `timeout_ms` of the evolve block (the same bound).
- Memory: Count-Min + Bloom + Welford + `reservoir` records. No growing log file in v0 (`build/traces` is the reservoir dump, capped).

If `cusum_alarm` is false, `propose` returns `NoChange` without calling `model`.

## Self-hardening (what may change)

From `report` + reservoir, a candidate may:

1. **Tighten** `requires` (add conjuncts) if still a bool expr.
2. Insert `core.limit.allow(...)` in a `task` body (impl) for an HTTP/auth task.
3. Add a union variant already used in traces (`Throttled`) to a public return type **only** via spec (breaking, visible).
4. Never drop `egress` entries, never add `ffi`, never store secrets in traces.

Fitness: fewer trap-1/2 and fewer `Denied`/`Throttled` *false* paths is **not** automatically known. v0 fitness is: check passes AND CUSUM on **contract traps** does not worsen on the reservoir replay (same recorded events, no live network). Replay uses stored tags only (S30: no mock world).

## Auth app (pattern, not a second language)

Spec still owns auth. Typical shape:

```
task login(req: LoginReq) -> Session | Denied | Throttled
    effects { database.read, secret, time, observe }
    requires core.str.len(req.user) > 0
```

Impl: `core.limit.allow`, `core.secret.get`, compare via stdlib-only KDF if present later; v0 compares only through `egress` verifier or `store.db` token. **No homemade ciphers in app code** (Kerckhoffs: don’t invent crypto). Hardening adds throttling and tighter `requires` from traces (repeat `Denied` on the same hashed key → Bloom + token bucket), not a new hash algorithm.

## What the coding agent sees

`ObserveReport` + reservoir (≤ `reservoir` rows) is the capsule (charter bounded context). It shows:

- high-count tags (Count-Min) = hot sad paths
- `cusum_alarm` = distribution shift (attack or bug)
- reservoir rows = example blind spots
- never a raw password or `Secret`

The agent proposes spec/impl **candidates**; the checker is the proof gate.
