# Resource lifetime and accounting design

Status: preimplementation design for issue #5. The current VM traces managed values
and embedding pins; `validation/resource-lifetimes/before.json` records its native
and traced-type surface. A host-only helper or an unexposed mapping prototype does
not satisfy the requested language API or aggregate accounting requirements.

## Value and ownership model

Introduce opaque nominal resource handles through a normative amendment, compiler
stdlib declarations, verifier/VM type handling and native implementation together.
Handles are not constructible as records, JSON serializable, structurally comparable
or convertible into pointers or integer descriptors. Ordinary v0 programs require
no lifetime annotations. Existing bytes remain immutable managed values.

Each native allocation has a VM-owned control block with its budget reservation,
state, immutable identity, payload and synchronization. Buffer/view handles refer
to this block with checked offsets and lengths. Nested views flatten offsets without
copying ownership. Copies of a handle alias the same logical handle state; creating
a view creates an independent view handle. Closing a view invalidates that view's
aliases, while independently created children retain their explicit control-block
reference. Closing the owning resource invalidates all access through its views.
Define these distinctions in the language amendment and test them explicitly.

Each access acquires a bounded operation lease under the control-block lock and
checks both handle and resource state before touching native memory. Close marks
closed before admitting further access, waits for already admitted accesses or
uses deferred physical release, and releases storage exactly once when leases end.
No raw host memoryview or buffer export escapes into application or embedding code.
An embedding read yields copied bytes or a scoped checked lease with an explicit
release contract. Exported handles need VM pins; raw host references are not roots.

Immutable sharing permits concurrent reads. Mutable buffers use the same lock to
serialize reads and writes and fixed-size bounded updates; they cannot resize while
views exist. An explicit freeze produces immutable access and revokes mutable
operations for every alias. It must not silently leave a writable alias behind.

## Mappings and descriptors

Mapping APIs must establish an immutable backing object before sharing; a read-only
mapping of an externally writable file does not establish immutability. The existing
Linux sealed-column prototype is reference evidence only, not a shipped language
implementation. Define supported platform profiles and fail closed where required
sealing/isolation primitives are absent. Do not silently substitute an unsafe shared
file mapping. A copying fallback, if supported, must be explicit and budgeted.

Descriptors remain owned by control blocks and never become language integers.
Closing/unmapping must reject later accesses with a documented typed error. A failed
constructor must unwind every acquired descriptor, mapping and reservation before
returning or propagating cancellation. Unknown partial state must not be published
as an initialized handle.

## Budget contract

Use atomic reserve-before-allocation and exactly-once release on close/GC/failed
initialization. Explicit limits cover native allocated bytes, mapped address bytes,
open descriptors and handle/view counts. Views count as handles but do not duplicate
the parent payload's byte charge. Scratch copies and simultaneous old/new buffers
must be charged during transitions; successful-output size is not peak allocation.
Track current/high-water usage and failed reservations without exposing payloads.
Define inherited/shared budgets for parallel tasks and embedding contexts.

Issue #5 requires an inventory and integration audit across supported native resource
paths, including existing filesystem/network/storage scratch and descriptor ownership.
A budget for only a new buffer class must not be reported as an aggregate runtime or
RSS budget. Document actual reservation units and unavoidable host overhead, and
qualify OS limits separately from native resource accounting. The initial control
block implementation is a foundation, not issue closure.

## GC and lock ordering

Extend tracing to retain view-to-owner edges, native argument/result handoffs and
embedding pins. Reclamation schedules idempotent close of unreachable resource
handles. Do not wait on a native operation while holding the heap mutator lock;
otherwise a completing native waiting to publish its result can deadlock GC.
Specify one lock order and deferred-release queue, then test that queue on normal
collection, exceptions, cancellation and VM teardown. Physical release must remain
reachable until completion, even after the language handle becomes unreachable.

## Reference evidence

CPython `Objects/memoryobject.c` at
`823f0323ee6ec1402088b73bce1a38473cac36dc`, `_memory_release`, tracks exports and
marks released views before relinquishing the managed buffer. Its
`Modules/mmapmodule.c` tracks exports and refuses unsafe close with exported views.
These are references for the separation between logical close, outstanding access
and physical release. GoPyT's explicit checked-handle policy is its own contract;
Python reference counting or the GIL is not a substitute for it. No donor source
or dependency is adopted.

## Required qualification before closure

Freeze a normative API and operation/state transition matrix before implementation.
Exercise actual compiled GoPyT buffers/views/mappings, nested alias close, immutable
and mutable sharing, races between close/access, GC during native handoff, pins,
partial initialization at every allocation/open/map step, native exceptions and
cancellation. Independently verify data and exact budget return after each case.
Use real concurrent workers and process/platform checks for mapped profiles, not
only mocked return values. Preserve failed trials and source identities. Avoid
performance or safety claims beyond the tested workload and platform boundaries.
