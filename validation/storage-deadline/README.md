# Storage deadline admission validation

Runtime fingerprint: `a24339e0c691b394a2042e0a5a01f1bd953b01e6785faaf05ef46720de2c2e72`.

The compiled `store.db.put` probe holds the VM store's local operation lock for
300 ms while the calling task has a 100 ms VM deadline. Before this change it
returned timeout at 303.881455 ms and a reopened Store contained the new value
(`before.py`, `before.json`). After the change it returns timeout at 100.28534 ms
and the value remains absent (`after.py`, `after.json`). The after probe changes
only its output path. These are single diagnostic observations, not production
latency measurements or a sustained workload qualification.

Six new tests cover plaintext and encrypted stores. The lock tests keep local
locks and real process advisory locks held until the caller returns; cancellation
in the process-lock case is triggered only after an actual nonblocking flock
attempt reports contention. They require timeout/cancellation before releasing
the peer lock, no durable mutation, no pending temporary files and successful
reconciliation afterward. Further cases cancel after snapshot loading or expire
the VM deadline after a temporary file's fsync, requiring no publication and
cleanup. Existing publication/cancellation tests still pass when cancellation
occurs inside replacement: committed state survives and receipt reconciliation
prevents duplicate effects.

All 58 focused storage, transaction, publication and cancellation tests pass on
Python 3.14.7 and 3.11.16. Guard calibration (17), stdlib parity (21 modules),
reproducible builds, installed wheel smoke and upgrade/rollback also pass. Two
builds at epoch 1788998400 produce identical wheel SHA256
`0c0fc9809aae98ad69b5afce0b8c8168b018c5937b269cbad574b4723e6b6c2c`
with 46 runtime files. The full Python 3.14.7 regression suite passes all 831
tests in 318.839 seconds; the contracts demo also passes.

The [normative policy](../../docs/parallel-admission-amendment-2026-09-10.md#storage-admission-and-publication)
retains the five-second store lock budget, adds inherited context checks around
bounded waits and before publication, and requires finishing durability/cleanup
once replacement is entered. Individual disk, SQLite and encryption operations
remain noninterruptible. Store context is a weak reference to its owning VM and
uses the calling thread's VM state; it does not retain the whole VM. Standalone
host stores have no implicit VM context. This increment does not prove fairness,
sustained cancellation-storm resource budgets or complete all-native shutdown.
Issue #13 remains open.

`source-reference.json` records the upstream reference identity for timed lock
acquisition and nonblocking flock wrappers. No dependency was added, and no
reference source was copied or executed.
