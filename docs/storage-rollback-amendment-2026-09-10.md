# Trusted snapshot generations and authorized restoration

This amendment adds opt-in rollback detection to authenticated snapshot storage.
It preserves the existing typed native results, bounded snapshot format and
transaction ambiguity rules. The earlier [design and threat model](storage-rollback-design.md)
was committed before implementation. This document specifies the implemented host
interface; qualification evidence is recorded separately.

## Operator trust boundary

Set `GOPYT_STORE_ANCHOR_DIR` to an absolute, existing private directory outside the
application package, dedicated to one `GOPYT_STORE_ID`. The directory and its state
must be outside application backup/rollback scope. Set the existing encryption key
or keyring configuration and stable store ID as usual. Anchor directories require
private operator ownership; record/lock files require private single-link regular
files. Path traversal does not follow symlinks.

The adversary may replace, delete or restore application snapshots and local
metadata, but cannot modify/roll back this trusted directory, operator environment,
or encryption keys. Restoring the entire host or trusted authority defeats that
assumption. Same-user hostile code requires OS isolation; this mechanism does not
supply it. The runtime does not infer filesystem backup policies or promise that
an arbitrary outside directory is an independently protected authority.

Without this environment setting, existing encrypted storage behavior remains:
old valid ciphertexts can be replayed. Strict mode still requires encryption but
does not implicitly provision an anchor. Selecting, removing or changing anchor
configuration is a trusted operator action, never a language-native operation.

## Record and publication contract

`record.json` is limited to 4,096 bytes, rejects duplicate/extra/malformed fields,
and contains version 1, store identity, generation, ciphertext SHA-256 digest,
recovery staging name and latest restoration metadata. Generation starts at zero
and advances through 9,223,372,036,854,775,807; exhaustion rejects further writes.
A null digest at generation zero represents an explicitly enrolled empty store.
A record created from an existing authenticated snapshot has its digest at zero.
Later generations contain a digest and `.pending-` plus 24 hexadecimal characters.

Store locking precedes authority locking. Authority waiters consume the existing
store/VM deadline and cancellation context. Every operation validates the authority;
ordinary reads, including cache hits, hash the bounded snapshot. Loaded ciphertext
is checked again before decryption. Thus cache hits incur a snapshot scan. No
latency or throughput improvement is claimed.

A write fsyncs its new encrypted staging file and containing directory, advances
and fsyncs the authority record, then replaces the snapshot and fsyncs its directory.
Once authority advancement is admitted, it is not abandoned for ordinary deadline
cancellation. Failure may leave an advanced authority and staged or committed data;
a failed return does not prove that the logical change failed.

Before ordinary access and pending-file cleanup, recovery validates the current
digest. If it differs, only the exact staged ciphertext named by the authority can
be published. Missing or substituted evidence fails closed, with no old-generation
fallback. The authority directory durability barrier is repeated during recovery.
Unavailable, malformed or missing authority state fails as `DbError` at the native
boundary; cached data is not served. Normal startup never enrolls a missing record.
Cryptographic authenticity still depends on the configured authenticated-encryption
keys, and digest binding assumes SHA-256 collision resistance.

## Operator commands

Stop application writers before initial enrollment or a planned restore. Commands
use the same store/authority locks, but stopping writers prevents a maintenance
plan from racing new application effects. Configure the key, store identity and
anchor directory in the trusted environment, then run:

```sh
python3 -m gopyt.store_admin --root /srv/app enroll
python3 -m gopyt.store_admin --root /srv/app status
python3 -m gopyt.store_admin --root /srv/app restore \
  --backup /backups/store.sqlite3 --expected-generation 42 \
  --reason 'Approved recovery after incident INC-123'
```

Enrollment authenticates and validates an existing snapshot or explicitly enrolls
an empty store. It cannot overwrite an existing authority. Status reads trusted
metadata without requiring a usable application snapshot; it does not attest
snapshot availability. Commands emit JSON on success and exit 2 on maintenance
failure. No new GoPyt native grants these operator powers.

Restore requires authenticated backup bytes for the same store context, valid KV
schema/string values, the exact current generation and a nonblank UTF-8 reason
of at most 512 bytes. It can deliberately replace missing/replayed application
state but cannot bypass unavailable authority or a stale expected generation.
It re-encrypts and publishes the chosen logical contents as the next generation.
The old backup ciphertext itself remains ineligible for ordinary replay.

The authority retains latest restore metadata across ordinary writes: previous/new
generations, backup digest and reason. Each admitted restore also produces a trusted
`restore-<generation>.json` receipt; recovery recreates a missing latest receipt
from the authoritative record before serving data. A receipt records admitted
restoration intent, not proof of HTTP delivery or a power-loss durability test.
Conflicting receipt contents fail closed. Earlier receipts are not automatically
pruned; operator archival/retention is required. Do not store credentials or personal
data in restoration reasons.

Loss or rollback of the trusted authority requires explicit operator trust
reestablishment outside this protocol. Copying an old authority beside a restored
backup must not be described as verified rollback detection. A remote authority,
hardware monotonic counter, host-compromise defense, power-loss qualification and
production recovery SLOs remain separate deployment controls or future adapters.
