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
