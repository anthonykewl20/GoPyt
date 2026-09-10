Buffer copy admission follow-up

copy_view admits immutable output and an equal temporary byte-copy capacity before
copying a contiguous one-byte memoryview. The exact bytes temporary is discarded
inside its reservation; the owned subclass copy retains its reservation through
aliases. Tests cover source mutation, view release, copy overlap admission failure
and cancellation with retained exceptions. Both pinned Python resource-bytes suites
pass. This helper is not yet wired into Buffer/View reads; bytes-input compatibility
and complete buffer-copy qualification remain pending. PR76's JSON source is kept
unchanged in its separate worktree while its CI runs.

Buffer and View reads now use copy_view under their existing operation lease and
serialization lock. Returned bytes retain their charge after view/owner close.
Initial focused runs failed the 64-byte parallel-write fixture's old 128-byte
budget; its read now requires 192 bytes for owner/temporary/output overlap. The
fixture budget was updated and a separate below-overlap rejection test added.
All 22 focused tests pass on both pinned runtimes (read-fixed logs), including
read-result survival after close and reservation cleanup. Initial failure logs
remain. Byte-input compatibility and broader integration are still pending.

Buffer/View writes and Buffer.map_bytes now accept immutable bytes subclasses,
including admitted read results, without converting away their ownership. Mutable
bytearray inputs remain rejected. All 24 focused tests pass on both pinned Linux
runtimes, including read-to-write, view writes and read-to-sealed-mapping. The input
bytes remain charged after source/target/mapping closure until the last input alias
is dropped. Broader fault, cancellation and full-suite validation remain pending.
