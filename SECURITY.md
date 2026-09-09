# Security scope and operation

Cybersecurity is a release priority. GoPyT is experimental and has not received an independent security audit. Its VM is not a replacement for an operating-system sandbox for hostile code.

## Strict backend profile

Install the `security` extra. Supply these environment variables to the trusted host process:

- `GOPYT_SECURITY_PROFILE=strict`
- `GOPYT_STORE_KEY_FILE`: absolute path to exactly 32 random raw bytes.
- `GOPYT_STORE_ID`: stable, deployment-specific storage identity.
- `GOPYT_HTTP_TOKEN_FILE`: absolute path to a random 32..256 character printable ASCII service token.
- `GOPYT_HTTP_ADDR=127.0.0.1:8080` (or another numerical loopback address).

Keep secret files outside the application package, owned by the service account or root, mode 0600, with no symlink path components or hard links. Generate secrets using an operating-system cryptographic random generator. Supply the token as `Authorization: Bearer <token>`. Missing, incorrect and duplicate headers receive 401 before application handling. Do not commit secret files or put tokens in URLs.

Strict mode refuses missing keys/tokens and non-loopback listeners. Terminate TLS at a trusted gateway and enforce external access control there. The shared service credential does not implement individual users, tenant authorization, MFA, rate limits or credential provisioning. Development mode remains the default for local examples; it permits plaintext storage and anonymous HTTP when credentials are absent.

Storage snapshots use AES-256-GCM-SIV through the separately selected `cryptography` security extra. A random nonce and the store identity authenticate each snapshot. Wrong keys, wrong identities, corrupted ciphertext and plaintext under an encrypted configuration fail closed. Writes retain the storage lock, atomic replacement and fsync protocol. Existing plaintext databases are not automatically migrated. Back up keys separately and test restoration; losing the key loses access. A private bounded keyring and explicit `gopyt-store rekey` operation support the [maintenance rotation procedure](docs/storage-key-rotation.md). Do not replace a live single-key file with unrelated key material.

An optional private `GOPYT_DB_POLICY_FILE` confines database reads and writes to operator-selected namespaces, including before cache access. See the [batch and authority amendment](docs/batch-storage-amendment-2026-09-09.md). This is service authority, not HTTP user or tenant authentication. An unset policy preserves package-wide access; strict mode alone does not restrict database namespaces.

## Trust boundaries

Application file natives reject hard links, symlink traversal, internal state and Git metadata access, and writes to source, build and package metadata. VM effects still govern native access. These checks assume a trusted host account and protected package directories. Another process with the same filesystem authority can race or replace files; root can inspect memory. Use isolated service accounts, containers or equivalent OS controls for untrusted workloads.

Encryption protects snapshot confidentiality and integrity against an attacker without the key. It does not prevent deletion, replay of an older valid snapshot, memory inspection, malicious authorized requests, or exfiltration by a compromised host. Plaintext exists in RAM. Disk encryption, swap/core-dump policy, gateway TLS, tenant authorization, backup retention, monitoring and incident response remain deployment responsibilities.

Mapped-data experiments are plaintext and are not exposed as a GoPyT native. Read-only mappings alone do not secure a mutable file. The Linux prototype seals its backing object before mapping and sharing; authenticated encrypted queries need a separate design.

The LSP accepts local, bounded package snapshots and invokes the checker with CPU/time limits plus a Linux address-space limit. It provides editor assistance, not an untrusted multi-tenant compilation service.

## Reporting

Do not publish credentials or exploit details in an ordinary issue. Private vulnerability reporting is enabled: use the repository Security tab to report privately. Dependency vulnerability alerts are also enabled.

Release compromise, withdrawal and replacement handling follows the [release provenance policy](docs/release-provenance.md). Consumers must check current source/artifact revocations as well as signed identity.

Opt-in [trusted snapshot generations](docs/storage-rollback-amendment-2026-09-10.md) detect application-snapshot replay when the operator protects a separate authority from rollback. The baseline replay exclusion above continues to apply without that configuration and to rollback of the authority itself. Explicit restoration advances the authority rather than disabling detection.
