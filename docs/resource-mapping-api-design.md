# Immutable mapped buffer API plan

Status: implementation contract for the resource buffer amendment, issue #5.
The native is implemented in the working tree; full qualification remains open.

Add `data.buffer.map_bytes(data: bytes) -> Buffer | ResourceError`, with
`effects { resource }`. It creates an immutable snapshot backed by a private sealed
Linux memfd, then a read-only mapping. It requires no application file path or raw
descriptor, and does not grant filesystem authority. The returned value uses the
existing opaque Buffer type, view operations, tracing edges and VM ownership rule.

The initial mapped profile requires Linux memfd creation and successful kernel
sealing. Missing primitives or rejected seals return ResourceError without publishing
a handle. Python builds lacking named fcntl constants may use verified Linux UAPI
values behind the Linux platform guard. Other platforms return ResourceError for
this explicit mapped operation. Ordinary allocate/write/freeze remains available
as an explicit copying alternative; map_bytes must never silently substitute it.
Empty input returns ResourceError because a zero-length file cannot be mapped by
the selected constructor. This restriction does not change zero-length allocate.

Reserve before creating the backing object: input-length native bytes for backing
storage, input-length mapped bytes for the mapping, two descriptors for original
memfd plus CPython's duplicated descriptor, and one owner handle. The managed input
bytes remain separately managed. Do not claim mapped address size is RSS, or count
the same backing pages twice as native bytes merely because they are mapped.
Reserve transient copies separately if implementation introduces them. Populate
with scoped memoryviews and bounded writes; handle partial writes and cancellation.

Apply WRITE, GROW, SHRINK and SEAL seals and verify the complete seal set before
mapping. No writable mapping or backing descriptor is exposed to callers. Once
mapping succeeds, close the original descriptor and release its separate charge;
retain backing bytes, mapped bytes, one descriptor and one handle until physical
mapping close. Admission must account for both descriptors simultaneously even
though steady-state ownership is one. The Linux host probe under
`validation/resource-lifetimes/mapping-admission/` verifies this peak on both pinned
Python versions, but is not implementation qualification.

| Operation/state | Required result |
| --- | --- |
| Read owner or nested view while open | Copied managed bytes matching the snapshot |
| Write owner or any view | ResourceError, data unchanged |
| Freeze open mapping | Success; remains immutable |
| Close view | Invalidate its aliases; independently created child stays usable |
| Close owner during admitted access | Reject new access; defer unmap until leases end |
| Access after owner close | ResourceError through every alias/view |
| GC or VM teardown | Use existing deferred close ownership; no unmap under heap lock |
| Failure or cancellation during initialization | No published handle; unwind acquired stages and charges |
| Uncertain physical cleanup | Retain a reachable cleanup owner and reservation; report failure |

Initialization needs explicit ownership transfer at each acquisition. In particular,
constructor exceptions and the native's post-construction cancellation check must
not drop the only reference to a control whose cleanup failed. The implementation transfers unfinished constructor cleanup through
MappingInitializationError to the native handler and heap deferred queue.
Descriptor close failures quarantine the original reservation and never retry the
raw descriptor number. A fault test closes and reuses that number, then verifies
cleanup retry leaves the replacement descriptor intact. The conservative charge
remains until the host reports unresolved teardown; there is no automatic claim
that an ambiguous close succeeded.

Qualification must run actual compiled map_bytes calls, exact-data and immutable
alias checks, descriptor/mapping counter observations, capacity rejection before
acquisition, partial writes, failures at every acquisition stage, cancellation,
concurrent close/access, nested views, collection and teardown. Preserve initial
failures and source identities. The Linux profile must pass real kernel sealing
checks; unsupported-profile behavior must also be tested. Broader native accounting
from resource-accounting-audit.md remains required for issue #5 closure.
