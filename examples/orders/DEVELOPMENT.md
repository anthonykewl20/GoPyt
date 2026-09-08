# Unmeasured application development record — 2026-09-05

Public declarations for all ten modules were written before implementation
bodies. The author read the frozen contract and did not read the independent
oracle or its acceptance corpus. Division of authorship is within a shared
workspace and model family; it is not organizational isolation.

Toolchain: gopyt-0.1.000, bytecode 2. Package digest:
`sha256:68139bb36823480399a0fb8d031e290f1b2a734d5acb532baea10080b271f5be`.

Changed public specs: all ten new `spec/orders/*.gopyt` files. Changed
implementation files: all ten new `impl/orders/*.gopyt` files. Added
`test/orders/service.gopyt`, transport adapter, public Python tests, manifest,
compiler-proposed lock and documentation. Runtime/compiler changes: none.
Open contract IDs: none. Unresolved implementation IDs: none. Skipped public
tests: none. No measured trials were run or edited.

Development failures, retained as observations rather than scored trials:

1. The initial source-generation helper failed with Python `TypeError` because
   `stock` was supplied twice as a keyword argument. No bodies had been written.
   Merged the defaults and override before calling the helper; rerun exited 0.
2. `PYTHONPATH=. python -m gopyt fmt examples/orders` exited 1 with
   `gopyt: unexpected argument`. The CLI takes no root-path argument. Subsequent
   commands used the application working directory and repository PYTHONPATH.
3. The first application-directory fmt exited 1 with `GOPYT_E017 keyword`,
   `impl/orders/fulfillment.gopyt:9`. An enum value at the end of an `if`
   condition was parsed as a payload constructor followed by a reserved field
   name. Bound each enum comparison predicate before `if`, preserving business
   behavior without changing the compiler. Subsequent fmt succeeded.
4. Missing-lock checks returned expected `GOPYT_E041` with the complete repair.
   The first proposed digest was
   `4fa9ca2de71b98b3c72b3b7d68ef963aba98972cd5b5319439c94af44122841f`.
   Public test additions changed source before the lock was created. The final
   proposed repair was written verbatim; final checks passed.
5. A check with public tests named `test/orders/lifecycle.gopyt` exited 1 with
   `GOPYT_E018 extra_impl`: the test module had no matching spec module. Renamed
   it and its module header to `test/orders/service.gopyt`; subsequent check
   reached only the expected missing-lock diagnostic.
6. A source search included nonexistent `tests/`, yielding an `rg` path warning;
   actual compiler tests live elsewhere. No verification claim came from it.

No business/public test execution failed. Nine GoPyT tests and nine Python tests
passed in a fresh copy, after successful fmt and check. Exact commands, exits
and captured output are in `development-validation.json`. The standard GoPyT
CLI emits no per-test success output; the nine declarations were checked in
`test/orders/service.gopyt`, and its `test` command exited 0. This record reports
public development checks only; independent business acceptance is owned by the
controller and is not claimed here.

## Amendment 2026-09-06 — executable obligations

Public specs gained `ensures` clauses on `transition`, `refund`, `pay`, `cancel`,
`quote` and `place`; `obligations.json` and six tests were added. No business
behavior, request or response field changed. Failures observed while doing it:

1. The first amendment script asserted on a `use` line that differed between
   spec and impl (`impl/orders/service.gopyt` imports more names). The spec file
   had already been rewritten; `gopyt check` reported `GOPYT_E032 impl_header`
   until the impl twin received the identical clauses. Fixed by merging names
   into each file's own `use orders.model { ... }` line.
2. The first acceptance run (`evidence/development-03/`) was started while the
   test file was still being edited; the evaluator recorded
   `source_changed_during_evaluation` and `passed: false` even though all 3,908
   scenarios matched. It is retained as a failed attempt. `development-04/` ran
   on the final sources and passed.
3. `python -m gopyt.obligations report` initially flagged `Reply`/`Request` as
   generic declarations because the detector looked for `[` anywhere before the
   first parenthesis (`list[Snapshot]`). Fixed to match a type parameter list
   directly after the declared name.

Package digest after the amendment is recorded in `gopyt.lock`.
