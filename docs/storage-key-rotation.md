# Authenticated snapshot key rotation

The trusted host can use `GOPYT_STORE_KEYRING_FILE` instead of
`GOPYT_STORE_KEY_FILE`. Selecting both is an error. The keyring is a private,
single-link file outside the application, at most 4096 bytes:

```json
{"version":1,"active":"new","keys":{"old":"<64 hexadecimal characters>","new":"<64 hexadecimal characters>"}}
```

The placeholders above are not usable keys. Generate each key from 32 bytes of
OS randomness. There must be 1..4 distinct keys, names of 1..32 ASCII letters,
digits, underscores or hyphens, and an active name present in the map. Duplicate
JSON fields, duplicate key material and unknown fields are rejected.

Writes use the active key. Reads try the active key, followed by at most three
retained keys, accepting only authenticated decryption. The original snapshot
framing and stable `GOPYT_STORE_ID` authentication context are retained. Existing
single-key snapshots remain readable when that key is in the ring. The cache
identity includes the complete ring and active selection; retiring a key cannot
leave its old plaintext available through a cache hit.

## Maintenance procedure

1. Back up the current ciphertext and key material and verify restoration.
2. Quiesce all writers. Prepare the private ring with old and new keys, select
   the new active key, and configure every writer consistently. Preserve the
   store identity. A stale writer with an old active key can otherwise publish
   another old-key snapshot.
3. Run `gopyt-store rekey --root /absolute/package/path` with this host
   configuration. It authenticates and validates the existing snapshot, then
   republishes under the active key using the normal lock/fsync protocol. If a
   namespace policy is configured, maintenance requires both read and write `*`.
4. Verify application reads from a fresh process with a ring containing only
   the new key. Then restart writers with that ring. Retain old keys securely
   for any retained backups that still require them.

The command reports `committed` only after successful publication and directory
fsync. On error, retain all keys and inspect/reconcile the store before retrying.
A failure near publication may leave the old or new authenticated snapshot.

The operation neither migrates plaintext nor creates an absent database. It
does not change the store identity, rotate HTTP credentials, protect against
old authenticated snapshot replay, or revoke ciphertext copies already obtained
with a compromised key. Production key custody and external rollback protection
remain separate architecture work.

## Fenced authority profile

The [writer-key fencing amendment](storage-writer-fence-amendment-2026-09-10.md) adds an explicit authority-version transition and online stale-writer rejection for consistently configured authority-aware writers. The quiesced workflow above continues to describe unanchored/version-1 maintenance; ordinary rekey cannot silently change a version-2 permitted writer.
