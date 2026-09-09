# Concurrent transaction histories and cancellation qualification

This evidence supports issue #7's bounded batch contract. It extends the
publication fault matrix; the goal includes all seven original acceptance and
evidence criteria. It does not qualify a general transaction API, scalable new
storage backend, hard native I/O deadlines, or production disaster recovery.

## Independent state oracle

`gopyt/test_transaction_histories.py` uses a dictionary specification, independent
of SQLite queries, storage locking, caches and file publication. For each history
it exhaustively searches permitted sequential orderings of at most eight completed
calls. An operation completed before another was invoked must precede it. An
ordered batch read must match all dictionary values. A successful CAS requires all
expected values to match, then changes/deletes all values; a false CAS requires
at least one mismatch. A complete ordering is a witness, not a timing guess.
The checker rejects empty/oversized histories and invalid intervals.

Negative controls reject stale reads after a completed write, simultaneous false
successes, torn batch observations and false conflicts. A control accepts an old
read overlapping a write by ordering it first. Another distinguishes absence,
empty strings, deletion and unchanged read conditions.

The real workload has three duplicate deliveries, each reading a snapshot then
submitting a conditional balance transfer plus absent-receipt condition. The
final read observes the complete state. Twelve fixed trials per execution/storage
combination produce 48 seven-call histories: independent Store threads and separate
Python processes, each with plaintext and strict authenticated storage. Every
history must admit a witness, contain exactly one successful delivery, and finish
with exactly one transfer and receipt. Changing one observed failed CAS to success
must cause rejection of that same history. `histories.json` retains all invocation
and completion timestamps, arguments, results, initial states and witness orders.
Monotonic timestamps come from processes on the same machine, not distributed
clocks. There are no retries or tuned thresholds to make a history pass.

The oracle is an original small exhaustive specification interpreter. No donor
implementation or dependency was adopted. Existing storage design references are
retained in the batch amendment. The eight-call bound deliberately makes the
search complete for these histories; it is not a scalable production verifier.
This does not check arbitrary long executions, every possible schedule or opaque
failed calls. The separate fault and cancellation fixtures reconcile those calls
through actual state observations rather than inventing successful responses.

## Compiled cancellation and timeout paths

`gopyt/test_transaction_cancellation.py` compiles a task that updates balance and
receipt in one real native batch. Six tests repeat under plaintext and strict
storage. They check cancellation before call entry, cancellation immediately
before and after actual rename, cancellation hiding a post-rename sync error,
local-lock timeout yielding the typed `DbError`, a prepublication load error
hidden by cancellation, and a `parallel` timeout during publication. The timeout
probe waits for the real child cancellation event before allowing rename to
finish. The parent must then report the declared timeout trap. Every case reads
through a new Store, retries the original compiled task and verifies exactly one
final transfer. Cancellation and timeout intentionally do not promise rollback.

Initial cancellation fixtures omitted the language-required `time` effect on the
parallel task and were rejected with E050. `initial.log` retains that failure;
the fixture was corrected, not the compiler's rule. No runtime changes were made.

## Validation artifacts

- `focused-3.14.log` and `focused-3.11.log`: complete new oracle/history/cancellation suites.
- `history-capture.log` and `histories.json`: retained successful measurement run.
- `full-3.14.log`: all 739 language tests passed in 232.467 seconds.
- `source.json`: source hashes frozen before full validation, Python/platform/SQLite identity.
- Guard, stdlib, contract demo and wheel/clean-install logs: repository validation gates.

This workload is a correctness fixture with exact state expectations. No real-data
throughput or production SLO is asserted. Existing retail measurements retain
their original bounds and unsuccessful trials; these tests do not increase their
retained-data size or qualify a deployment target. Source identities are frozen
before full validation and checked again after it. Finite passing tests are not
an independent security audit or universal correctness proof.
