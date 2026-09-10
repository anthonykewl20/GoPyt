# Checked buffer API integration plan

Status: pre-language-integration API design. The internal checked buffer/control/
reservation classes are implementation foundations, not shipped GoPyT natives.
This refines the resource lifetime architecture without closing issue #5.

The language amendment will add opaque nominal `data.buffer.Buffer` and
`data.buffer.View`, with a new `resource` effect covering allocation, observation,
mutation and close. Allocation is bounded host authority, not an implicit filesystem
or network capability. The compiler, bytecode effect validation and canonical effect
ordering must agree before any native is published. Values cannot be constructed,
structurally compared, serialized or converted to raw pointers/descriptors. Opaqueness
must propagate through nested records, unions and collections, as existing Secret
restrictions do, without accidentally declassifying resources through generic helpers.

Proposed tasks in `data.buffer`, all with `effects { resource }`:

| Task | Result |
| --- | --- |
| `allocate(size: i64)` | `Buffer | ResourceError` |
| `read(buffer: Buffer, start: i64, length: i64)` | `bytes | ResourceError` |
| `write(buffer: Buffer, start: i64, data: bytes)` | `unit | ResourceError` |
| `freeze(buffer: Buffer)` | `unit | ResourceError` |
| `view(buffer: Buffer, start: i64, length: i64)` | `View | ResourceError` |
| `subview(view: View, start: i64, length: i64)` | `View | ResourceError` |
| `read_view(view: View, start: i64, length: i64)` | `bytes | ResourceError` |
| `write_view(view: View, start: i64, data: bytes)` | `unit | ResourceError` |
| `close(buffer: Buffer)` | `unit | ResourceError` |
| `close_view(view: View)` | `unit | ResourceError` |

`ResourceError` is an explicit status type, not a leaked Python exception. Range,
closed, frozen and capacity errors leave data unchanged. Cancellation retains the
VM's cancellation behavior. Close is idempotent logical invalidation; already
admitted operations complete before physical release. A failed physical cleanup
keeps its charge and control block available for retry; VM teardown must report
unresolved cleanup rather than silently discarding it. Closing a view does not
close its owner or independently created children. Closing the owner invalidates
all children. Freeze is global to the owner and revokes mutation through all views.

The embedding API must accept an explicit shared ResourceBudget so parallel work
and child contexts cannot invent fresh capacity. Default host limits and inheritance
must be fixed alongside the native-path allocation inventory before integration is
qualified. A returned copied `bytes` becomes a managed VM value; the transient copy
reservation covers construction, not permanent Python object/RSS overhead. Buffer
payloads and open handles have independent charges. Closed handle wrappers may still
exist as managed values without owning native payload capacity.

Heap tracing follows each view to its owner. A native operation pins arguments and
results across heap-lock release. GC must queue unreachable handle closure and drain
outside the mutator lock; embedding pins retain the same resource graph. No Python
reference-count timing may determine language close semantics. Real compiled tests
must cover aliasing, nested opaque-type restrictions, use-after-close, freeze races,
GC during native access, cancellation, budget recovery and explicit teardown before
this proposed API becomes a supported language amendment.
