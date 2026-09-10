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
