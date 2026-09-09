# HTTP response serialization budget

A real HTTP handler returns a typed string payload of 8,388,609 bytes. Before the
change the server sends HTTP 200 and 8,388,622 JSON bytes. Afterward it sends an
empty 500 before success headers. The handler input is injected by a trusted test
wrapper into the compiled call; this isolates response serialization from request
size limits. The first fixture setup retained an unused `core.str.len` import
and was correctly rejected with E014; removing that unused import enabled the
probe. Before/after scripts differ only in report output filename.

Six regressions cover exact UTF-8 and escape byte boundaries, canonical list/map
output against Python's independent JSON encoder, oversized input rejection
before escaping/sorting, depth and callback cancellation, real keep-alive
rejection/recovery, and serialization expiry without success headers. All 187
focused tests pass on Python 3.14.7 (14.376 s) and 3.11.16 (15.170 s).

All 860 full regression tests pass on Python 3.14.7 in 308.485 seconds.
Guard calibration (17 tests), stdlib parity (21 modules), the contracts example,
reproducible wheels, installed-wheel checks and upgrade/rollback also pass. The
two wheels are bitwise identical and contain 46 runtime files; build-report.json
retains exact packaging identities. Source, test and build files stayed frozen
during the full run.

The [normative response policy](../../docs/parallel-admission-amendment-2026-09-10.md#http-response-serialization-budget)
limits encoded body bytes and traversal, not the handler's existing value graph
or aggregate RSS. A bytearray and immutable output copy, temporary escaping and
map sorting overhead coexist. Serialization failure does not undo prior handler
effects. No response streaming or automatic retries are added. This advances
issue #13's buffer scope without completing its fairness, all-queue/resource or
sustained qualification requirements.

The fresh [deadline inventory](deadline-inventory.json) pins this runtime and
updates the serving policy. Verify it with
`python tools/check_deadline_inventory.py validation/http-response-budget/deadline-inventory.json`.
The earlier deadline-boundaries inventory remains an immutable review of its
older runtime; its default source-drift rejection on newer code is intentional.

## CI worker-admission test correction

The first macOS/Python 3.14 CI run failed an older parallel test that assumed
a worker entered a noncooperative native within its five-millisecond deadline.
The caller instead timed out in 7.397 ms before that native ran. The retained
job log shows this sole failure after 860 tests; it does not demonstrate an
abandoned writer. The corrected test freezes deadline time until actual native
entry, waits for the real coordinator stop signal, and proves the caller cannot
return while the admitted writer is deliberately held. Releasing it must finish
publication, return trap 6 and release worker capacity. Real waits/joins remain
bounded; the test no longer assumes a scheduler admission latency.

All 19 focused tests pass on Python 3.14.7 and 3.11.16. The fresh full suite passes all 860 tests in 305.927 seconds; the contracts
example also passes. Every recorded packaging input is byte-identical, so the existing
reproduced wheel and installed-wheel/upgrade results still qualify this runtime.
