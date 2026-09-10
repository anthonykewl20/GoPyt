Buffer copy admission follow-up

copy_view admits immutable output and an equal temporary byte-copy capacity before
copying a contiguous one-byte memoryview. The exact bytes temporary is discarded
inside its reservation; the owned subclass copy retains its reservation through
aliases. Tests cover source mutation, view release, copy overlap admission failure
and cancellation with retained exceptions. Both pinned Python resource-bytes suites
pass. This helper is not yet wired into Buffer/View reads; bytes-input compatibility
and complete buffer-copy qualification remain pending. PR76's JSON source is kept
unchanged in its separate worktree while its CI runs.
