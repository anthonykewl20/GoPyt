The new Store.restore_anchor_file path requires a caller resource budget and reads
through read_payload with descriptor-aware regular_file. Charged input is borrowed
through the shared restore implementation, without converting to exact bytes.
The existing public restore_anchor API retains its exact-bytes contract.

31 payload/rollback tests pass on both pinned runtimes. Tests cover rejection
before consuming input, failed restore with a retained charged alias, and the
existing public input contract. Initial failures are retained: Mock call history
held the input after the explicit alias was cleared. Replacing that mock with a
direct failure hook isolates the intended alias without changing assertions.

CLI context/limits/registry cleanup, broader failure qualification and full
integration remain pending. This change does not complete issue #5.
