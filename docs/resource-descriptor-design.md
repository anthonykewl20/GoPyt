# Descriptor ownership integration

Status: next implementation contract for issue #5. The Buffer/mapping increment
does not yet charge descriptors acquired by existing file, network or storage paths.

## VM ownership and admission

Add a VM-owned descriptor registry sharing the VM's ResourceBudget. Each descriptor
has an internal owner allocated and registered before the OS acquisition. Reserve
one descriptor before each open, including simultaneous parent/child traversal
descriptors. Registry admission must happen before open, so registry allocation
failure cannot strand an acquired descriptor. If open fails, remove the empty owner
and release the unused reservation. Expose no raw descriptor in the language API.

The registry differs from language heap roots: it holds internal acquisition and
cleanup state throughout native calls, even before a language result exists. GC
must not close an in-progress acquisition merely because there is no managed root.
VM teardown may drain the registry only after call admission has stopped and active
calls have finished, matching the existing VM.close contract. Parallel calls share
the registry and budget; registration/removal is synchronized, while OS close runs
outside the registry and heap locks.

## Close and uncertain outcomes

An owner detaches its descriptor number under its state lock before issuing close.
Successful close releases the reservation and removes registry ownership exactly
once. A raised close leaves a quarantined owner and conservative charge, with the
error category recorded. Never retry a detached raw number: the OS may already have
released and reused it. Repeated owner close reports unresolved cleanup without
another raw close call. Idle VM.close must return false if quarantined ownership
remains; context teardown reports ResourceCleanupError. This is an explicit failed
cleanup result, not evidence that the descriptor was physically released.

Keep OS acquisition, body, and cleanup errors distinguishable internally. Cleanup
failure must not erase a cancellation or original operation failure; retain both
the original error and cleanup state without logging file contents or credentials.
A host must retain/report unresolved teardown. OS worker termination and isolation
are separately qualified controls, not a substitute for correct owner bookkeeping.

## First consumer: core.file

Thread the registry through regular_file and parent_directory for VM calls, while
keeping non-VM callers explicitly outside this first integration. Transfer each
parent/child owner only after child acquisition succeeds. If parent close fails,
the child still has a registered owner and must be closed during unwind. The target
file owner stays live until its stream closes; fdopen(closefd=False) does not own
the descriptor and must not release its charge. Reserve before creation/truncation,
and preserve the existing cancellation checks before mutation.

Descriptor capacity exhaustion uses allocation trap 14, consistent with shared
file-read scratch exhaustion; do not add a new result variant or effect to core.file.
Reads and writes share the same descriptor capacity with mapped Buffers. A failed
admission must neither create a missing output file nor truncate an existing file.
Administrative atomic_write, storage, network, accepted sockets and SQLite/TLS
internal resources remain separate required consumers of this ownership discipline.

## Qualification

Test actual compiled reads/writes with capacities zero, one and two; verify no
mutation before rejected admission, parent/child/target overlap, short I/O,
cancellation at each acquisition boundary, and return to the exact budget baseline.
Use real descriptors to inject a close that physically succeeds then raises, reopen
that number, and verify cleanup cannot close its replacement. Also inject open and
registration failures and parent-close failure after child acquisition. Run parallel
calls sharing a budget and verify aggregate admission and VM teardown reporting.
Retain evidence on Linux and macOS; do not infer one platform's ambiguous-close
behavior from the other or call conservative quarantine successful cleanup.

## Working implementation status

The descriptor registry is now attached to each VM and shares its construction-time
ResourceBudget. VM.close drains it after idle admission stops; unresolved descriptor
cleanup makes teardown return false. core.file read/write pass the registry through
parent-directory traversal and regular-file acquisition. Parent ownership transfers
to the child before closing the old parent, so failure still unwinds the child.
Body exceptions retain precedence while cleanup remains separately visible in the
registry. Non-VM file helpers and other native resource paths remain outside this
increment. Focused compiled tests cover zero/one/two descriptor budgets, rejection
without output creation/truncation, and parent-close failure with child cleanup.
Full and cross-platform qualification of this increment remain pending.

## Storage integration sequence

Storage key loading must use the same VM registry as the database, rollback anchor
and staging files. Integrate that path first without implying that remaining opens
are accounted. Descriptor capacity rejection during a store operation becomes its
existing DbError, like the existing database size limit; it must occur before the
rejected open and must not expose key material. Private-file validation remains
unchanged. Direct administrative callers without a VM retain their existing API
and are explicitly outside this initial registry integration.

Key-file and snapshot-file opens now use the VM registry. Snapshot acquisition
recognizes FileNotFoundError only while opening; exceptions raised by its caller
propagate unchanged and still close the descriptor. Directory, database-lock,
rollback-anchor, receipt and publication-stage descriptors remain to be integrated.
This partial implementation does not establish aggregate storage resource accounting.
