# Observation deadline validation

A compiled limiter task waits behind a held observation lock before it can run.
With a 100 ms VM deadline and 300 ms lock hold, the original caller returns trap 6
after 300.388067 ms, only after peer release. The repaired caller returns after
100.163709 ms while the lock is still held. Neither consumes a limiter token.
These single controlled observations do not establish a physical scheduling SLO.
The after probe additionally forwards the new context keyword through its spying
wrapper; workload and synchronization are unchanged. Reports retain source and
probe hashes.

Five new regressions cover compiled call cancellation, note/report/snapshot/outcome
contention, original-trap preservation, acquisition-expiry cleanup and release of
an unreturned heap handoff after an already-performed limiter effect. The first
broader run used stale example/conformance toolchain locks; its three failures
are retained. After refreshing only the lock toolchain lines, 183 focused tests
pass on Python 3.14.7 (17.184 s) and 3.11.16 (20.011 s).

All 854 full regression tests pass on Python 3.14.7 in 336.046 seconds. Guard
calibration (17 tests), stdlib parity (21 modules), the contracts example,
reproducible wheel builds, installed-wheel checks and upgrade/rollback also pass.
The two wheels are bitwise identical, with 46 runtime files. Source, test and
build files stayed frozen during the full run; exact packaging inputs and hashes
are retained in build-report.json.

The [normative policy](../../docs/parallel-admission-amendment-2026-09-10.md#observation-admission-and-terminal-telemetry)
permits omission of trap/HTTP telemetry when its context cannot admit it, preserves
original outcomes, and creates no deferred writer. Sketch algorithms are unchanged.
These sketches are not a lossless audit log. Admitted updates and heap cleanup
remain completion barriers; this does not qualify every native or all sustained
resource/fairness requirements of architecture #13.
