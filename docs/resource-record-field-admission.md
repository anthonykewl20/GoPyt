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
for unrelated producers of those values. Native status, observation, storage and
HTTP record construction paths still require a separate admission review. Full
and platform qualification remain pending for this increment; focused tests cover
the reproduced tracing failure, compiled records/enums, budget rejection, empty
variants and cancellation cleanup.
