# Storage cipher destination-buffer capability

The pinned cryptography 50.0.1 AESGCMSIV implementation exposes encrypt_into
and decrypt_into on both pinned Python versions. The retained probe verifies
one roundtrip against its allocating API. This is API capability evidence,
not a cryptographic oracle, failure qualification or native allocator bound.

The next implementation can reserve caller-owned destination capacity before
encryption/decryption and use scoped memoryviews for framing slices. It must
preserve keyring fallback, reject invalid authentication before publishing
plaintext, clear partially written destinations on failure, and retain charges
for surviving aliases. Plaintext clearing must not require another full-sized
allocation. Existing snapshot format and writer identity must remain stable.

SQLite allocator usage and the cryptographic library's internal scratch still
require separate accounting or a qualified isolation policy.

The keyring wrapper now offers encrypt_into/decrypt_into. Failed operations
clear the supplied writable destination in at most 4096-byte chunks; failed
reader authentication clears it before trying another key. Tests cover real
key fallback, matching existing allocating output, partial writes and cancellation
with retained destination aliases. Eleven focused tests pass on both runtimes.
These methods are not yet connected to budgeted storage allocation.

VM storage loads now decrypt into preadmitted mutable plaintext and clear it
on scope exit. Surviving aliases remain charged until destruction. Scoped
memoryviews avoid ciphertext/nonce slice copies. The unencrypted path borrows
existing input. Thirty-five focused tests pass on both runtimes; the initial
failed injection against the retired unseal call is retained. The injection
now targets deserialization with the same alias assertions. Ciphertext output,
restore, SQLite and cryptographic internal accounting remain unfinished.

VM storage saves now reserve framed ciphertext and nonce capacity before
encryption, using encrypt_into and retaining the ciphertext charge through
publication. The borrowed ciphertext is cleared on scope exit; aliases keep
their charge until destruction. Forty-seven focused tests pass on both runtimes.
Earlier plaintext recovery coverage passed 66 tests on each runtime. SQLite
serialize output is still allocated before this ciphertext boundary, and
restore plaintext and native-library internals remain unfinished.

Normal loads and anchored restore now share _deserialize_snapshot, which
uses admitted plaintext for VM contexts and clears local aliases on exit.
Thirty-four focused cipher/rollback/publication/payload tests pass on both
runtimes. Restore backup input and SQLite serialize output are still outside
this plaintext reservation, as are native-library internals.
