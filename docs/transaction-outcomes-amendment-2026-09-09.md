# Transaction outcomes, cancellation and reconciliation

This amendment makes the existing bounded `store.db` batch contract explicit.
It supplements the [batch amendment](batch-storage-amendment-2026-09-09.md)
and [storage amendment](storage-amendment-2026-09-05.md). It adds no API, isolation
level, opcode, hard I/O deadline or general transaction object.

## Admission, publication and acknowledgment

A batch reads and compares one snapshot while holding the cooperating-process
lock. All conditions match or no changes publish. A changing batch builds a new
in-memory snapshot, serializes and (in strict mode) authenticates it, exclusively
creates a pending file, writes, flushes, fsyncs and closes that file, renames it
over the live snapshot, and fsyncs the containing directory. Rename publishes
the batch to subsequent cooperating readers. Successful directory fsync completes
the requested durability protocol. A no-op matched batch returns true without
publication. Value-CAS has application-managed versions for ABA protection.

The following outcomes assume cooperating processes and a supported local
filesystem honoring the documented rename/fsync operations. Hardware or kernel
failure does not become a rollback guarantee.

| Boundary when execution fails or stops | Visible state on the running host | Caller action |
|---|---|---|
| Rejected input, authority, authentication, or lock acquisition | No changes from this attempt | Resolve the cause before retrying |
| Condition mismatch | No changes from this attempt; return false | Read a fresh snapshot, recompute and use bounded retries |
| Snapshot load, mutation, serialization, encryption, pending create/write/flush/file fsync/close, or before rename | Old complete published snapshot | Reconcile receipt before retry if the precise failure boundary is not known to the caller |
| After rename, before successful directory fsync | New complete snapshot visible; survival of a system crash is uncertain | Outcome is ambiguous; inspect receipt and state after recovery |
| After successful directory fsync but before acknowledgment | New complete snapshot; response may be missing | Inspect receipt; do not repeat business effects |
| True result delivered for a changing batch | New complete snapshot and durability protocol completed | Treat operation as committed within the storage contract |

`DbError` intentionally does not expose a reliable commit-stage discriminator.
An error can follow publication, including failure of directory fsync. Similarly,
a transport disconnect or lost response cannot tell the client whether a native
ran. A process exit before rename leaves the old snapshot and possibly a pending
file. After rename a live-host restart reads the new snapshot. Recovery acquires
the same lock and removes recognized pending names before serving operations.
The process-exit tests do not emulate power loss or claim that un-fsynced rename
survives it. Already acknowledged records may require external backup recovery
after media failure; disaster recovery is separately tracked in #25.

## Cancellation and timeouts

The VM checks inherited cancellation before calls, at bytecode instruction
boundaries, and after a native returns. Storage I/O inside an admitted native is
not interruptible by VM cancellation. Therefore cancellation before entry has no
write effect, but cancellation during native execution may allow the complete
batch to publish and then discard either its success result or its `DbError`.
Do not infer rollback from `Cancelled`.

A `parallel timeout_ms` expires its cancellation event, stops admitting arms,
waits for already started arms, and then traps 6 (`TRAP_TIMEOUT`). If an arm is
inside a storage native, the timeout can occur before publication and the write
can still finish before the parent reports the trap. This is structured cleanup,
not a hard wall-clock I/O deadline. A stuck kernel I/O call can delay that return;
#13 tracks deadline propagation and stronger resource execution budgets.

Store mutex/flock contention uses the current five-second lock-attempt budget
and returns `DbError` when the acquisition times out, without changing data.
This does not impose a five-second deadline on file I/O, serialization, encryption,
or an already acquired transaction. An external supervisor killing the process
uses the process-death outcomes above; it must still reconcile ambiguous commits.

## Idempotent recovery

Choose a unique request identifier and put its receipt and all business changes
in the same batch, with an absent-receipt condition. Compare every aggregate
value read by the decision, including application version fields when needed.
After any uncertain outcome, read the receipt and associated state using a
consistent `get_many`. A matching receipt means the operation already applied.
When absent, recompute from a fresh snapshot and submit a bounded conditional
retry. A conflict does not mean success: repeat reconciliation up to the chosen
retry budget and return an explicit unresolved/conflict result when exhausted.
A reused identifier for different input must be rejected by application policy;
storing a request digest in the receipt supports that check. Retain receipts for
at least the supported replay/retry window; deleting them permits a later duplicate
to apply again. External side
effects are outside this batch's atomic boundary and need their own protocols.

## Evidence and limits

[Publication evidence](../validation/transaction-publication/README.md) exercises
58 actual-file I/O-error and abrupt-process-exit scenarios in plaintext and strict
storage. [History and cancellation evidence](../validation/transaction-histories/README.md)
records real concurrent histories, independent specification orderings, negative
controls, compiled cancellation and timeout cases, and the complete test results.
Neither finite histories nor fault injection prove universal correctness, every
filesystem's durability, an OS sandbox, or production throughput and recovery SLOs.
