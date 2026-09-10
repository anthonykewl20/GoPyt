# Explicit plaintext storage migration design

Status: proposed implementation, frozen before runtime edits. The baseline under
`validation/plaintext-storage-migration/` proves that encrypted reads and rekey
reject plaintext and that no migration command exists. Ordinary encrypted access
must continue to reject plaintext; migration is an explicit trusted host action.

## Operator contract

Add `gopyt-store migrate --root ROOT --backup ABSOLUTE_PATH --expected-digest SHA256`.
The digest identifies the exact original plaintext SQLite snapshot bytes. The
operator obtains it after quiescing all application writers. Require a valid target
storage key and stable store identity. Require an encrypted recovery-copy path
outside the application package, in a private operator-owned directory. Never log
keys or database values. Require wildcard read/write authority if a database policy
is configured. No GoPyt native exposes migration.

This is the initial unanchored plaintext-to-SIV1 transition. Reject configured
rollback authority: an enrolled encrypted authority cannot legitimately describe a
plaintext database. After migration and a fresh-process read, the operator may
explicitly enroll trusted version-2 authority and use fenced key transitions.
All writers remain quiesced until their encrypted configuration is installed and
verified. This requirement is necessary for writers using the pre-migration profile;
key fencing applies only after common authority enrollment.

## Validation and publication

Acquire the existing store lock and validate descriptor identity, size, schema and
all key/value types. Reject missing or malformed source data, unexpected tables,
wrong expected digest, missing/wrong keys, symlinks, unsafe recovery-copy permissions
and incompatible settings before publishing a replacement. SQLite integrity checking
must validate the whole source, not only a single application key. Accept exactly
the supported SQLite KV schema; no arbitrary SQL conversion or format guessing.

Encrypt the exact validated original bytes rather than a reserialization, so the
operator's digest remains independently checkable after decryption. Durably create
a private encrypted recovery copy before touching the active file: exclusive private
staging, file fsync, no-overwrite publication, parent-directory fsync. Existing
recovery files must authenticate to the expected plaintext digest and supported
schema; never overwrite an unrelated or invalid recovery copy. Reuse its exact
ciphertext for the active replacement, avoiding ambiguous multiple nonce variants.

Stage the same ciphertext under the store directory and publish using its existing
atomic replacement and directory durability barrier. Success means the recovery
copy and active encrypted snapshot have both completed their durability barriers.
No plaintext backup is created. Existing plaintext copies, filesystem snapshots and
free blocks are not securely erased by replacement; their disposal is an operator
storage-policy concern.

## Retry and recovery

The required digest and explicit recovery-copy path identify the operation. A retry
validates both before changing state. Before the recovery copy is committed, the
active plaintext remains authoritative. Once the copy is durable, an interrupted
active replacement leaves plaintext or the exact admitted ciphertext. A retry may
publish the authenticated recovery copy over matching plaintext, or reconcile an
already encrypted source whose decrypted bytes match the expected digest. An absent
active file may be recovered only from that existing authenticated copy. A present
mismatching/corrupt active file is never silently replaced; operator investigation
and an explicit recovery decision are required.

Wrong keys, lost recovery evidence or digest mismatch fail closed. An error after a
rename does not mean the operation rolled back; retain configuration and recovery
copy, inspect, and retry the same identified operation. Recovery staging left by a
process crash contains ciphertext only. Name cleanup must not delete another
operation's evidence. No automatic downgrade back to plaintext is provided.

## Supported format transitions

Plaintext SQLite KV to SIV1 is the initial migration. SIV1 key changes retain the
ciphertext framing and use rekey or fenced authority maintenance as documented.
Authority v1 to v2 is an explicit metadata transition, not a cipher migration.
Unknown ciphertext versions are rejected; introducing a future cipher format needs
its own authenticated transition and recovery protocol. Do not label rejection of
unknown formats as implementation of a future format.

## Required evidence

Test the real host API and CLI, independent decryption plus full SQLite row
comparison, fresh-process encrypted reads, preserved ordinary plaintext rejection,
wrong digest/key/schema/type/permissions/configuration, preexisting recovery copies,
retry after admitted publication, missing active-file recovery, and actual process
crashes around both backup and active durability/publication barriers. Test contention
and cancellation using the existing store deadline rules. Freeze source identities
before qualification. Retain unsuccessful trials with their causes. Process exits
are not power-loss testing; no production recovery-time or security certification is
claimed by finite functional tests.
