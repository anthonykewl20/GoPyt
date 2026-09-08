# auth — the P3 shape

A login task that keeps its sad paths as **types** (`Denied`, `Throttled`),
rate-limits with `core.limit`, stores tokens in `store.db`, and reports its own
health through `core.observe`. It matches the pattern in `docs/hardening.md`.

No homemade crypto: the token comparison is `==` on values the app was given.
The module lists no `ffi` and no `egress`; it makes no network call at all.

## Run it

```
cd examples/auth
python3 -m gopyt check      # types, effects, contracts, lock -> build/out.gobyte
python3 -m gopyt test       # three login tests on the VM
python3 -m gopyt run auth.burst
```

`burst` drives 40 logins for one user through a 4-token bucket. The refused
attempts are `Throttled`, the VM records each refusal as an `observe` failure,
and Page CUSUM (`p0=0.02, k=0.01, h=8`) alarms, so the task prints `true`.

## What it demonstrates

| Rule | Where |
|------|-------|
| sad paths are types, not exceptions | `login -> Session \| Denied \| Throttled` |
| scalar union member matching (S7) | `match stored { str -> … NotFound -> … }` |
| stdlib `ffi` does not propagate (S9) | `verify` lists `database.read`, never `ffi` |
| least privilege | `compare`/`deny` hold only `observe` |
| bounded telemetry (S32) | `core.observe.report().cusum_alarm` |
| private helpers stay out of `spec/` (S15) | `verify`, `compare`, `deny`, `attempt`, `key_of` |

`gopyt/test_examples.py` runs all of the above and asserts the observe buffers
do not grow with the number of events.
