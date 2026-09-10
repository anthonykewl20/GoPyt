# Heap-lock batch validation

PR #68 at 56167eb failed both Linux/Python 3.11 jobs on inventory HTTP response
timeouts during concurrent reservations. Six other checks passed. Both failed CI
logs are retained. The diagnostic-coverage failure wraps the same inventory failure.
The request timeout and all inventory assertions remain unchanged.

The published source passed five local acceptance runs, with maximum observed
request time 7.055 seconds. The parent ad60065 passed three comparable runs with
maximum 3.190 seconds. One-CPU runs also passed (PR maximum 3.455, parent 2.833).
Readiness attempts vary, so raw request counts are not identical workload sizes.
These sequential local trials are diagnostic comparisons, not production SLO proof.

A nested-call admission experiment did not establish an improvement (maximum 7.597
seconds); later trials overlapped focused tests and are confounded. Skipping empty
drain calls also did not establish a reliable fix (maximum 6.706). Both experiments
were reverted and their source snapshots retained. Instrumented active-stack samples
were dominated by storage and heap lock waits; sampling can perturb execution.

The bounded 32-instruction heap-lock batch candidate passed three unchanged acceptance
runs (7.075, 4.518 and 4.555 seconds total; maximum request 4.491 seconds). That trial
used runtime 5decb4c213b4fa58aa4f9ab2884ba6696d5390d2f428c46c0f2f0dd83b4fe16e.
A subsequent docstring correction produces final runtime
7412d5dd22b74c77dd4bc60f087595cbfe619caa97f242c4d22fe88cc0076ccb.

The final runtime passed all 966 tests on Python 3.11.16 in 408.711 seconds and a
162-test VM/heap/resource/parallel selection on Python 3.14.7 in 6.220 seconds.
Source hashes were checked before and after the full run. Tests cover physical lock
release around native scopes, exception and early return, and deferred resource
cleanup after batch release. All 17 Guard boundary tests, 22-module stdlib parity,
ticket-contract cases, wheel smoke and upgrade/rollback checks passed. Repeated
wheel builds were identical: d6d1202c076a8e79ccd07a62fec3d6ee52dea08257ab1613197d728781e81c4a.

Fresh exact-head platform CI is still required. These finite trials do not establish
universal fairness, wall-time bounds, RSS accounting, or complete issue #5 scope.
The raw probe scripts use temporary output paths; retained source and results identify
the executed trials. The previous inventory belongs to its historical runtime; a
separate reviewed inventory must accompany the updated source commit.
