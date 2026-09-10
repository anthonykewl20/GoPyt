# Owned record and enum field arrays

`NEW_RECORD` and `NEW_ENUM` admit their destination field slots before copying
operands. Construction reads directly from the still-rooted operand stack into
an owned list, then replaces the operands with the nominal value. There is no
intermediate unowned stack slice. Insufficient capacity raises the allocation
trap, and cancellation discards an unpublished partial array. Zero-field variants
need no field-slot reservation.

An owned field array is a tracing child of its record or enum. Merely visiting its
elements was insufficient: a host could pin the array independently, release that
pin, and have collection clear it even while its parent remained pinned. The
retained baseline reproduces that loss. Tracing the owned array itself preserves
both its storage and its children through the parent. Array aliases still follow
ordinary explicit embedding-pin rules. Plain host-supplied field lists retain their
historical internal metadata representation; this change adds a tracing edge for
owned payload arrays produced by the runtime and typed JSON conversion.

The reservation uses the shared list-growth policy, retaining old and replacement
capacity through growth. Field values are borrowed; this change does not account
for unrelated producers of those values. The shared native constructor now admits field arrays for status, observation,
evolution, storage snapshot, HTTP response, FromStr, money and typed-time results.
Storage snapshots also admit the optional-value result list. Under total byte
exhaustion, constructing a nonempty error record can itself raise the allocation
trap; descriptor exhaustion with available field capacity retains its typed error.
Returned native fields remain charged until heap/host ownership ends. Buffer/View
capacity-error records and allocations producing the field values themselves still
require separate review. Full
and platform qualification remain pending for this increment; focused tests cover
the reproduced tracing failure, compiled records/enums, budget rejection, empty
variants and cancellation cleanup.

## Budget refusal, sweeping and overload

Charged managed allocations stay registered in the VM heap until an explicit mark
and sweep, so a record's owned field array remains charged after the record is
dead. Field arrays make byte pressure independent of the heap's object-count
collection threshold: a workload can exhaust the configured byte budget while
holding far fewer than the threshold number of dead objects. Every bytecode and
native admission site therefore sweeps the managed heap once when a reservation is
refused, and retries before trapping. Only the refusal path pays for the sweep.

A refusal that survives the sweep raises the existing allocation trap with an
explicit overload marker. The trap code, bytecode meaning and CLI exit status are
unchanged. HTTP serving answers an overload trap with 503 and a closed connection,
exactly as it already answers worker-admission refusal and every other resource
limit in that path; the fixed language allocation ceiling keeps returning 500,
because repeating that request cannot succeed. Retained request-scoped charges are
released by the VM's own mark and sweep, not by Python cyclic collection.

