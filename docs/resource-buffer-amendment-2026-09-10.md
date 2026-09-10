# Opaque buffer and view language contract

Implementation status: compiler/declaration integration in progress; native execution
and complete VM teardown are not yet qualified. Do not treat this working amendment
as a published release guarantee. It refines the frozen resource lifetime and API
designs; issue #5 remains open until the full implementation and evidence are complete.

Add `resource` after `observe` in canonical effect order, using bytecode effect bit
12. Bits 0..11 retain their existing meaning; unknown bits 13 and above reject.
The toolchain source fingerprint changes, so existing bytecode must be rebuilt.

`data.buffer.Buffer` and `data.buffer.View` are toolchain-defined opaque nominal
kind-3 values. The permitted kind-3 type names are exactly those two and
`core.secret.Secret`. A user-defined record with an opaque name, a forged record
constructor or another arbitrary kind-3 name must not create a resource handle.
Opaque resources do not support structural equality, including when nested in
records/collections/unions. They cannot pass to ordinary JSON encoding/decoding,
logging or string sinks; the existing opaque-value rejection diagnostic E112 also
applies to resources. This does not make them credentials or allow `secret.reveal`.

The eleven `data.buffer` tasks and `core.status.ResourceError` are declared in the
stdlib module fence. All tasks require the `resource` effect, including reads and
close because their result depends on mutable resource lifetime. Argument ranges,
closed handles, frozen writes and exhausted resource budgets return ResourceError;
VM cancellation continues to propagate through its existing cancellation boundary.

Ownership, alias close, global freeze, admitted access, deferred physical cleanup,
embedding pins and budget rules follow the resource lifetime and buffer API designs.
Ordinary v0 code needs no lifetime annotations. No pointer, descriptor or raw host
memoryview may escape. The next qualification increment must execute these tasks
through compiled programs, verify loader rejection of forged resources/effects,
and complete VM budget/teardown integration before exposing them as usable runtime
functionality. Mapping and existing native-path accounting remain required scope.

## VM allocation configuration

The host may pass a shared `ResourceBudget` to `VM(resource_budget=...)`. Parallel
arms within a VM share it. The initial default limits are 256 MiB native bytes,
512 MiB mapped address bytes, 256 descriptors and 65,536 open handles. These limits
are reservation policy, not measured RSS or a claim that existing non-buffer native
paths are already integrated. Buffer payloads, handles, transient buffer copies, and sealed mapping backing,
requested mapping lengths and owned descriptors are currently charged; the broader accounting audit remains open.

Resource handles belong to one VM heap even when budgets are shared. Cross-VM
arguments, including nested handles, reject before the receiving heap adopts them.
Embedding callers must pin retained results in their owning heap. Native results
receive nominal type IDs for union dispatch; closed/frozen/range/capacity errors map
to ResourceError without exposing payloads. Explicit teardown and final platform
qualification remain required before release.

## Explicit embedding teardown

`VM.close()` returns false without changing admission while VM calls are active.
Once idle teardown begins, new calls reject. All owned resource handles, including
pinned resources, are queued for close and physical cleanup runs outside the heap
mutator lock. A false result after idle teardown means physical cleanup is still
pending; the host must retain the VM and retry or report the failure. Pins continue
to protect managed object identity but do not authorize access after VM closure.
Repeated successful close is idempotent. CLI run/test and project test receipts use the VM context manager. The scope includes
long-running service tasks: teardown occurs after the task returns, not after each
HTTP request. Incomplete context teardown raises ResourceCleanupError retaining
the VM for embedding inspection/retry, and command/receipt error handling reports
failure. External embedding code must use the same ownership discipline.

## Immutable mapped snapshots

`map_bytes(data: bytes)` implements the Linux profile specified in
[the mapping API design](resource-mapping-api-design.md). Nonempty input becomes
a sealed immutable backing object and read-only mapping, returned as an ordinary
opaque Buffer. Writes through any alias return ResourceError. Empty input and
unsupported or failed sealing return ResourceError; there is no implicit copying
fallback. Allocate/write/freeze is the explicit portable alternative.

The constructor admits two descriptors at peak, releases the original descriptor
charge after successful close, and retains one until physical mapping close. Native
backing and mapped dimensions charge requested byte lengths, not OS page rounding
or measured RSS. Read copies have the existing transient native-byte reservation.
Failed initialization with unfinished cleanup transfers its owner to the VM's
deferred queue. A raw descriptor close that raises has uncertain ownership: it is
never retried by descriptor number, which may have been reused. Its conservative
charge and cleanup failure remain visible through unsuccessful VM teardown; a host
must report that condition rather than claiming successful release.

Both Python versions have focused Linux tests for compiled calls, nested views,
immutable writes, active leases, budget rejection, partial initialization and
cancellation. Full release and broader native accounting qualification remain open.

## Existing file-read scratch accounting

`core.file.read` uses the VM's shared native-byte budget for each bounded immutable
read chunk and the simultaneous final output copy. A short read atomically reduces
its reservation to the returned length; retained chunks remain charged until their
references are cleared after output construction or failure. Chunk capacity is at
most 65,536 bytes and may be smaller when remaining budget is low. Admission is
atomic even when a concurrent task consumes capacity after chunk-size selection.

Native-byte exhaustion raises allocation trap 14, extending that trap's existing
per-value ceiling to this explicit host budget. The task's existing result union
and filesystem effect remain unchanged. Successful returned bytes become managed
values; these scratch reservations do not measure Python object metadata, allocator
arenas or RSS. File descriptor ownership and write-path accounting are still under
audit and must not be inferred from this read-buffer integration.

## Bounded heap-lock scheduling

The VM may retain its single mutator-lock acquisition across at most 32 bytecode
instructions. Cancellation, root admission and automatic-collection checks still
run at every instruction. Native/nested calls release that acquisition through
the existing scoped release mechanism; frame return or exception releases it in
a finally block. A batch does not promise atomic application behavior.

Deferred resource cleanup runs after physical lock release, at a batch boundary
or frame exit. Pending resources retain their reservations until cleanup finishes;
programs must not assume GC releases capacity at the immediately next instruction.
Explicit close and lease rules are unchanged. This is an instruction-count bound,
not a wall-time bound for a synchronous opcode or host call. See
[the scheduling design](heap-instruction-batch-design.md) and retained failure and
qualification evidence under validation/resource-lifetimes/heap-batches/.
