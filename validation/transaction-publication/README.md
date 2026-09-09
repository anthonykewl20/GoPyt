# Snapshot publication fault qualification

This advances issue #7 A2/A4 and E1. It does not close the issue.

`gopyt/test_storage_publication.py` runs 58 fault scenarios: 15 abrupt process
exits and 14 raised I/O errors, each in plaintext and strict authenticated storage.
Tests use actual temporary files, SQLite serialization, authenticated envelopes
in strict mode, rename and fsync. The test wrappers call the original operations
before/after the selected edge. The partial-write case writes and flushes half a
snapshot before stopping. The child exits with code 73 without Python cleanup;
the parent verifies that exit code rather than mistaking a missing hook for success.

The seven publication operations are exclusive pending-file creation, write,
flush, file fsync, close, replace and directory fsync. Each has before/after
process-exit probes. Raised errors cover both edges except after successful
open: a real failed open does not return a descriptor, so a wrapper that discards
a successfully returned descriptor would invent a descriptor leak. A separate
partial-write probe covers interrupted buffered output.

Each scenario begins with two balances of 10 and no receipt. The candidate batch
moves one unit and writes the receipt together. The independent expected state
is exactly `[10,10,absent]` before rename and `[9,11,done]` after rename. Both a
warm Store and a new Store read all three keys; neither may observe a torn batch.
Recovery must remove pending snapshots. A retry using the original expectations
succeeds exactly when the first attempt did not publish, and the final state is
exactly `[9,11,done]` in either case. Reads and retries also establish lock release
and continued operation after each injected fault.

## Limits

These are deterministic publication-edge cases on a live operating system.
`os._exit` bypasses language cleanup but does not simulate kernel death, power
loss, controller caches, filesystem corruption, or failed hardware. After rename
but before successful directory fsync, a live-host reader sees the new snapshot;
its persistence after a system crash remains ambiguous. Even a simulated error
after successful directory fsync can hide an already durable result from callers.
A `DbError` is not a rollback guarantee. Use a receipt in the same atomic batch
and reconcile before retrying business operations.

Initialization (state directory creation), lock acquisition, encryption failures,
concurrent-history checking and VM cancellation/deadline behavior are outside
this new matrix. Existing suites cover some of these, but complete issue #7
qualification remains open. No throughput or production-SLO claim is made; there
is no tuned workload or external dataset in this correctness fixture.

The probes are original test instrumentation around the repository's existing
publication implementation. No upstream code was adapted and no dependency was
adopted. Prior publication design references remain in the batch amendment.

## Observed outcome table

| Injected event | Live-host read after recovery | Receipt-based retry |
|---|---|---|
| Before or after pending create, write, flush, file fsync, or close | Entire old batch | Applies once |
| After a flushed partial write | Entire old batch | Applies once |
| Before replacement | Entire old batch | Applies once |
| After replacement | Entire new batch | Conflicts; receipt is present |
| Before or after directory fsync | Entire new batch | Conflicts; receipt is present |

No API return reaches the client in the abrupt-exit trials. Error trials produce
`StorageError` at the host Store boundary (the language native maps this to
`DbError`); they do not report success even when the new state is visible.

Commands and raw logs:

- `python3 -m unittest gopyt.test_storage_publication gopyt.test_storage_transactions gopyt.test_storage -v`: `focused-3.14.log` (40 passed).
- Same focused modules under Python 3.11.15 with the existing security extra pinned to cryptography 50.0.1: `focused-3.11.log` (40 passed).
- Full language discovery: `full-3.14.log` (719 passed in 304.315 seconds).
- Guard boundary suite, stdlib parity, contract demo, wheel build and clean-wheel smoke: corresponding logs in this directory.
- `source.json` records interpreter/platform/SQLite identity and hashes of the runtime/test Python sources. Sources remain unchanged through validation.
