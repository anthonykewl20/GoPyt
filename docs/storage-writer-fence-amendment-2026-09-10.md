# Permitted storage writer key and authority version 2

This amendment extends trusted snapshot generation authority with a permitted
publishing key. It follows the [frozen design](storage-writer-fence-design.md)
and stale-writer reproduction. Snapshot ciphertext remains `GOPYT-SIV1`; this
change versions authority metadata, not the encryption algorithm or payload format.

## Profiles and compatibility

New `enroll` operations create version-2 authority records and authorize the active
storage key selected by the trusted operator configuration. An explicitly enrolled
empty store is supported. Existing version-1 authorities remain readable and retain
their unfenced maintenance behavior until an explicit operator transition. Ordinary
writes do not silently upgrade or change the permitted writer policy.

Version 2 adds a required lowercase 64-character hexadecimal `writer` field to the
version-1 schema. It is SHA-256 of the exact concatenation
`b'GoPyt writer key\0' + active_key_bytes + snapshot_aad`, where the active key is
32 bytes and `snapshot_aad` is `b'GOPYT-SIV1\0' + store_id_utf8`. This identifies key
material and store context independently of keyring labels or retained reader keys.
It is an identifier, not a replacement for authenticated encryption or an additional
secret to distribute. Continue provisioning keys from OS randomness.

Version-1 anchor-aware runtimes reject the unknown version-2 record; they must be
upgraded before using the fenced profile. Runtimes predating authority support or
writers that omit its configuration are not supported writers in this profile.
Every writer must use the same trusted authority and store identity. A writer with
unmediated filesystem access can still destroy or replace files; the authority
then detects a mismatched snapshot and fails closed. This is not OS isolation.

## Publication and key transition

Under the store and authority locks, every ordinary mutation checks its selected
active-key identity against the version-2 record. This includes conditional writes
whose conditions would fail, rekey and authorized restore. A stale key configuration
fails as `DbError` through language natives before publication or generation change.
Retained reader keys may still decrypt current data; that does not grant permission
to publish with the formerly active key. Settings loaded before waiting for a lock
are checked against the authority after the lock is acquired.

To transition the permitted writer, provision a private ring containing the current
decryption key and the intended new active key. Inspect the trusted generation and
run the explicit host maintenance command:

```sh
gopyt-store status --root /absolute/package/path
gopyt-store fence-key --root /absolute/package/path --expected-generation 42
```

`python3 -m gopyt.store_admin` is equivalent to `gopyt-store`. `fence-key` requires
an enrolled authority, an exact current generation and a valid encryption setting.
It authenticates current data (or the explicitly enrolled empty state), re-encrypts
under the selected active key, and advances the generation with the new writer
identity in one authority-first publication protocol. A version-1 record becomes
version 2 through this action. Successful output is the new authority JSON;
maintenance failure returns exit 2. No language native can grant key authority.

After authority advancement, interrupted publication can recover only the exact
admitted ciphertext under that writer policy. Missing recovery evidence fails
closed; failure does not imply the old key remains authorized. Inspect and reconcile
rather than automatically replaying an operator action with a stale generation.
Changing the authority or deliberately authorizing another key is a trusted operator
power; do not reauthorize compromised keys.

The ordinary `rekey` command preserves its existing interface. In the fenced profile
it cannot change the permitted writer; use `fence-key` for that explicit transition.
Unanchored and version-1 maintenance still require quiescing writers and do not
claim online fencing. Retiring keys before authenticating/re-encrypting existing data
remains an error, not a recoverable substitute for retaining the required key.

## Backup and retirement procedure

1. Preserve a verified ciphertext backup and the keys needed for that backup under
   the existing custody/retention policy. Keep the authority outside backup rollback.
2. Provision the reader ring with the old and new keys and choose the intended new
   active key. Perform the generation-checked transition above.
3. Verify data from a fresh process configured with only the new key. Upgrade/restart
   application writers to the new active selection; stale configured writers reject.
4. Retain old keys only for backups that still need them. A deliberate restore uses
   old reader keys to authenticate its backup but republishes under the currently
   permitted writer key. It does not restore old writer authority.

The generation/durability and host-operation cancellation limits from the rollback
amendment still apply. This increment does not implement plaintext migration,
hardware custody, key escrow or production recovery objectives. Process-crash tests
and functional probes are not power-loss or independent security-audit certification.
