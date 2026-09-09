# HTTP serving context validation

Runtime fingerprint: `cd7220d595342b048b575aa3271e870eb41f308899ced7e5688fc05296970e7a`.

The retained pre-change probe gives the serving task a 300 ms inherited deadline.
It was still running at 600 ms and returned timeout only after manual shutdown
(`before.json`, source in `before-probe.py`). The repaired probe exits on its own
at 301.567231 ms in this trial and is already stopped when checked at 600 ms
(`after.json`). The post-change reusable tool also records its fixture identity;
source/runtime hashes are checked again after execution. Reproduce with
`python tools/http_context_probe.py --output /tmp/new-http-context.json`.
This is a finite Linux lifecycle check, not an OS latency or production SLO claim.

The compiled API regression covers both inherited deadline and ancestor
cancellation with an idle listener, a partial-input connection, and an active
handler blocked in a cooperative native. It asserts the terminal condition,
closed listener, joined workers and completed native cleanup. All 26 focused
HTTP/scheduling tests pass on Python 3.14.7 and 3.11.16. Guard calibration (17),
stdlib parity (21 modules), reproducible wheels, installed smoke and upgrade/rollback
pass. All 810 tests pass on Python 3.14.7, and the contracts demo passes.

The serving context is propagated to handler workers and checked on coordinator
service ticks. A newly accepted connection is closed before admission when the
context has ended. The coordinator leaves the normal serving loop through an
exception, preserving socketserver's shutdown-event cleanup; it never calls
blocking shutdown from that same thread. Source reference hashes are retained;
no donor code was copied or executed and no dependency was added.

Issue #13 remains open. This does not qualify whole-request budgets, uncooperative
blocking natives, graceful delivery of active responses or sustained native
resource limits. Closing a connection does not prove that a handler's durable
operation rolled back. See the [normative amendment](../../docs/parallel-admission-amendment-2026-09-10.md#serving-context-cancellation).
