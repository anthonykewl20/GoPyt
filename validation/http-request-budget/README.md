# Shared HTTP request budget

Runtime fingerprint: `e55164b5b24b1ddef38eb72becbcbde9ff2755fb5ae0bdc49b0ccd665ad1c89d`.

The pre-change queue regression (`before.log`) ran against the serving-context
implementation at `4e85da22ecf6293c3ddb689ab5986393420097cc`. A request held in a
one-worker queue beyond its 150 ms configured budget still returned 200 when
dequeued. The repaired test verifies that this connection closes and its handler
never runs. A preceding noncooperative handler deliberately exceeds its own budget;
its effect is not undone and no rollback is inferred from the closed connection.

Additional real runtime checks verify that two requests on the same keep-alive
socket receive strictly increasing absolute deadlines, and exercise a cooperative
30-second sleep stopped by a 200 ms request budget, and an unread socket pair
whose bounded send buffer forces a one-MiB response write to time out. The latter
uses the actual response writer under an inherited VM deadline. The serving
context test additionally asserts that the original ancestor cancellation event
survives handler dispatch. This catches the prior dispatch assignment that
replaced that event chain even though coordinator-driven stop still worked.

All 29 focused HTTP/scheduling tests pass on Python 3.14.7 and 3.11.16. Guard
calibration (17), stdlib parity (21 modules), reproducible wheels, installed smoke
and upgrade/rollback pass. The final full suite passes all 813 tests on Python
3.14.7; the contracts demo also passes. These are controlled lifecycle and backpressure checks, not a production
latency SLO, fairness measurement or universal memory/leak proof.

The [normative amendment](../../docs/parallel-admission-amendment-2026-09-10.md#per-request-budget)
defines first-request admission timing, keep-alive reset, inherited caps, expired
queue rejection and connection closure when output time is exhausted. Native
calls that do not poll can still delay completion and may publish before timeout.
Fairness, arbitrary handler output memory, graceful draining and broader issue #13
qualification remain open. Source references are recorded separately; no donor
code was copied or executed and no dependency was added.

The first full run (`initial-full-failed.log`) exposed a test-isolation problem:
the identity-expiry test patched the shared Python time module's nanosecond clock,
while socket input retained the real monotonic clock. The new common deadline
therefore appeared already expired to socket input. The expiry test now replaces
only resource_authority's clock namespace, preserving real networking time and
all existing expiry/revocation and unchanged-database assertions. No runtime check
was disabled. Diagnostic-audit failures in that run propagated the same four
identity subcase failures; they were not separate compiler diagnostic regressions.

The initial macOS Python 3.14 CI run (`initial-macos-ci-failed.log`) expired the
keep-alive test's 200 ms budget around a 120 ms sleep plus network processing.
That was a success-latency assumption, not part of the reset contract. The test
now observes the two handler deadlines, verifies they increase on the same socket,
and retains a separate real 30-second sleep/200 ms timeout check. This preserves
the fresh-budget and execution-expiry assertions without requiring sub-200 ms
successful network scheduling. The runtime and build identities are unchanged.

After the timing-test revision, all 813 tests pass locally again
(`revised-full314.log`). A final explicit non-null socket assertion prevents
silent reconnects from satisfying the keep-alive check; the subsequent focused
HTTP reruns include that assertion. No runtime source changed.
