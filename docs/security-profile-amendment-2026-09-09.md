# Host security profile amendment

This amendment adds host-level controls without changing GoPyT source syntax or the closed stdlib signatures. Deployment requirements are in [SECURITY.md](../SECURITY.md).

Application file natives require single-link regular files, including before truncation. Reads and writes reject `.git` and `.gopyt-state` path components. Writes additionally reject source/test/build directories, package manifest and lock, and executable/source artifact suffixes listed in `gopyt.natives._safe_path`. Rejection uses the existing file-status result. These constraints apply to application natives; trusted compiler output still uses its own atomic-write path.

The strict host profile requires a private external storage key and HTTP service credential. HTTP authentication failure produces an empty 401 response and closes the connection before body decoding or handler execution. Strict listeners require numerical loopback addresses. Existing method, body-size and routing validation remains in force.

Configured storage encryption authenticates each whole SQLite snapshot with a stable deployment identity. Existing locks, size limits, conditional updates and durability protocol remain. Stored ciphertext includes fixed framing, nonce and authentication overhead. Authentication or configuration failure maps to the existing database-error result. There is no silent migration of plaintext snapshots.

Cache hits revalidate current key material and storage identity. Key/context changes invalidate the cache. Neither this cache nor authenticated encryption supplies rollback detection against a restored valid historical snapshot.
