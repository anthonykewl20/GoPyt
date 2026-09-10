# Admitted operator restore input

The restore CLI currently reads a complete backup before invoking Store.restore_anchor.
That API requires an exact bytes object; reserving its length afterwards would not
admit the allocation. The next change must preserve this public byte-input API and
add a file-input route that owns admission and reading before restore.

Use one explicit operator context with a ResourceBudget and descriptor registry,
kept alive for the entire command because Store retains only a weak context proxy.
All store operations in that command must use the same context, including key reads,
locks, plaintext, serialized output and publication. CLI limits must be documented,
validated before I/O and surfaced as an ordinary maintenance failure. Cleanup must
close the registry after the operation, including failed acquisition and cancellation.
A byte budget does not establish a bound on SQLite or cryptographic allocator usage.

The file route opens through regular_file with the same descriptor registry, reads
with read_payload at MAX_BYTES + OVERHEAD + 1, and retains its owned payload through
restore and backup digest computation. Refactor the shared restoration validation
into a private implementation that can borrow the charged immutable bytes without
converting them. Keep exact-bytes validation on the existing public API. Clear local
backup aliases in finally blocks so retained tracebacks do not accidentally prolong
input ownership; actual retained aliases must continue to carry their reservation.

Required tests: budget rejection before file consumption; descriptor exhaustion
before open; invalid size and identity/authentication failure; cancellation after
read; retained aliases and tracebacks; existing restore generation/digest/receipt
and admitted-publication recovery behavior; CLI failure reporting and registry
cleanup. No success claim is made by this design. PR #73 qualifies serialization
payloads separately and does not cover this input producer.

The maintenance CLI now creates this shared context for every operation. Defaults
are 268435456 native payload bytes and 256 descriptors; --native-bytes and
--descriptors accept nonnegative i64 values. These are payload/descriptor limits,
not process RSS limits. The registry closes in finally, including KeyboardInterrupt.
Restore uses restore_anchor_file. Capacity failures return the existing maintenance
error status (2, or 1 for rekey). Thirty-nine focused tests pass on both pinned
runtimes, including zero-descriptor rejection and cleanup after interruption.
Full integration and additional restore failure qualification remain pending.
