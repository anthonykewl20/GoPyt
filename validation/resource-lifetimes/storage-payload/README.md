# Storage snapshot input admission

Focused storage and payload suites passed 29 tests on Python 3.14.7 and 3.11.16.
Initial failures retained here came from a mock retaining its call arguments;
the corrected test substitutes a function directly and keeps one explicit alias.
An earlier fixture used an unlinked temporary file, which the existing identity
policy rejected; the corrected fixture is a linked named temporary file.

Snapshot input reads now reserve shared byte capacity before reading. Decoder
failure retains charges for surviving input aliases. This does not account for
unsealed plaintext, SQLite allocations, serialization or cryptographic scratch.
Full qualification remains pending.

The broader transaction/publication/deadline/rollback/migration selection passed
54 tests on each pinned Python version; raw results are retained in broad*.log.
These checks do not qualify unimplemented encryption or SQLite accounting.

A subsequent cancellation test aborts after actual input consumption while
retaining the exception traceback. It verifies scratch release and preservation
of the caller-owned descriptor. All three payload tests pass on both runtimes.

Full Python 3.14.7 regression passed 1019 tests in 361.167 seconds. Frozen
source hashes were verified after completion. Python 3.11 full qualification
remains pending.

The runtime wheel reproduced byte-for-byte (SHA-256
`2aa129060cf1a22038abad939090eb41c9ddc627e7d073dae8f4c82f134b8fd9`,
57 runtime files, epoch 1788998400). Installed smoke and old/new/rollback
checks completed successfully; raw evidence is retained here.

Guard calibration passed 17 tests, stdlib parity passed for 22 modules, and
the contract demo passed its positive and negative cases. Raw outputs are retained.
