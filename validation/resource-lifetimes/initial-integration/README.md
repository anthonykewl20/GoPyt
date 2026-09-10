# Initial resource integration evidence

This increment implements checked opaque buffers/views, sealed Linux mapped
snapshots, shared reservation ledgers, explicit VM cleanup, and file-read scratch
accounting. Issue #5 remains open: descriptor ownership across existing natives,
SQLite/TLS/internal allocations, wider concurrency and failure campaigns, and final
platform/release qualification are incomplete.

At runtime `19d44ed55f0dfbe60d5bb15f15173b8645482b7db88136b4ee66401c1f7f607b`,
Python 3.14.7 passed the complete 963-test compiler/runtime suite in 345.349 seconds.
Both pinned Pythons passed the focused 132-test VM/file/budget selection (3.11.16:
5.429 seconds; 3.14.7: 4.623 seconds). This is not a full Python 3.11 or macOS run.
Source hashes were captured during the frozen full run and checked after it ended.

The 17 Guard boundary tests, 22-module stdlib parity, ticket-contract negative and
positive cases, installed wheel smoke, and upgrade/rollback smoke passed. Two wheel
builds at epoch 1788998400 were bitwise identical, with 54 runtime files and SHA-256
`ece4ad83fe1a858e27aff9753a78e3da71130254acd090a8af784afe578c67c2`.

The retained drain failure demonstrates that batch allocation failure previously
left cleanup disabled. Its fixed focused logs precede file-read integration; the
full current suite includes that regression. The sibling mapping-admission archive
contains an earlier primitive probe and its missing-fcntl-constant failure; it is
not a language API qualification result. Other diagnostic output inside full logs
includes intentional negative fixtures; use terminal unittest outcomes.

The manifest hashes raw artifacts; source.json identifies compiler/runtime/test
inputs. No throughput, RSS, universal safety or independent security certification
is claimed. The previous deadline inventory is stale for this new runtime and needs
a separate source review before publication.
