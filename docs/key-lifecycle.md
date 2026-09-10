# Key and credential lifecycle

This is GoPyT's supported operator procedure for the private-file security profile.
It defines responsibilities and recovery decisions; it does not certify a particular
production installation, external vault, backup service or hardware device. Runtime
key formats and rejection rules are normative in `security_config.py` and the linked
storage/HTTP amendments. Use isolated service accounts and protected host directories.

## Ownership and inventory

Assign a service owner for application availability, a key custodian for provisioning
and escrow, and a recovery reviewer who checks restoration evidence. Record actual
people or service identities in the deployment inventory. A single developer may
hold these roles locally; production operators should separate custody from ordinary
application access. Record the stable store ID, active key label, creation/activation
and retirement dates, backup identifiers/digests, authority location, software source
identity and custody reference. Never record secret bytes in this inventory.

The application account needs its current private credential files. Application
packages, Git, logs, command arguments, URLs and support tickets must not contain
secret material. Escrow belongs in separately access-controlled encrypted custody,
with its recovery secret protected independently of the application machine. The
filesystem profile does not implement hardware custody or protect against a host
administrator reading process memory. Test the chosen external custody restoration
procedure before relying on it; the repository drill uses isolated temporary
locations on one host and cannot qualify that external system.

## Provisioning

Provision the deployment secret directory outside every application package, owned
by the service account or root, with mode 0700 and no symlink components. Generate
32 random bytes for each storage key; generate a separate service token with at
least 32 random bytes encoded as hexadecimal (64 printable characters). Never reuse
storage keys as HTTP credentials or reuse either across deployments.

From a trusted operator process, the following creates a new private file without
printing the value or overwriting an existing file. `DIRECTORY` must already meet
the directory requirements. Run once with `KIND=storage` and a new key filename, and
separately with `KIND=http` and a new credential filename. These environment values
identify the destination; none contains a secret.

```python
import os, secrets
kind = os.environ['KIND']
if kind not in ('storage', 'http'):
    raise SystemExit('KIND must be storage or http')
name = os.environ['NAME']
if not name or name in ('.', '..') or '/' in name:
    raise SystemExit('NAME must be one filename')
directory = os.open(os.environ['DIRECTORY'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    value = secrets.token_bytes(32)
    if kind == 'http':
        value = value.hex().encode('ascii')
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(directory)
finally:
    os.close(directory)
```

This snippet assumes the operator has validated the full directory path; it is not
an untrusted-path resolver. Verify private ownership, modes and single-link files
before starting the service. Configure one storage key source, a stable store ID,
and strict security mode. Copy storage keys into encrypted escrow using the custody
system's authenticated transfer/import procedure; verify a fresh process can restore
from that custody before declaring provisioning complete. HTTP tokens need not be
escrowed for data recovery: a lost token can be replaced and clients reprovisioned.
Do not restore a revoked token merely because an old configuration backup contains it.

## Storage activation, rotation and revocation

For plaintext data, quiesce writers and perform the
[explicit migration](plaintext-storage-migration-amendment-2026-09-10.md). Preserve
and verify its encrypted recovery copy. Enroll a separately protected authority
before admitting application writers into the fenced profile. Keep that authority
outside the rollback scope of application backups.

For a key change, provision and escrow the new key first. Prepare the bounded ring
with old/new readers and the new active key, then follow
[writer-key fencing](storage-writer-fence-amendment-2026-09-10.md): inspect the current
authority generation and run `gopyt-store fence-key --root ROOT --expected-generation N`.
Verify a fresh process with only the new key, including restored backup contents,
before removing the old key from live writer configurations. Ordinary `rekey` cannot
grant a different writer in the fenced profile. Unanchored/v1 profiles require the
[quiesced procedure](storage-key-rotation.md) and make no online fencing claim.

When compromise is suspected, restrict affected writers at the host/gateway, revoke
their access to secret files and custody, provision a new independent key, and fence
the former publishing key. Investigate copies and host access separately. Retained
reader keys are decryption authority, even when no longer publishing authority.
Rotation cannot revoke plaintext or old ciphertext already obtained with a stolen
key. Re-encrypt retained backups where policy requires eliminating that dependency;
verify them before retiring the only usable old key.

## HTTP credential rotation and revocation

Provision a new private token file, distribute the new credential through an
authenticated secret channel, and atomically replace the listener's pinned token
path with a same-directory staged private file. Fsync the staging file, replace,
then fsync its directory. Follow the [live rotation amendment](http-credential-rotation-amendment-2026-09-10.md).
Verify new requests succeed and revoked-token requests return 401 on that same
listener, including keep-alive. Invalid/unavailable files return 503 without handler
admission. Already admitted requests may finish; use service isolation/draining
when the incident requires stopping those requests too. A shared service token is
not an individual user's identity or tenant permission. Revoke delegated user/session
authority through the corresponding identity controls as well as changing any
compromised shared credential. Never log Authorization headers or token values.

## Backup retention and retirement

The reference operating policy retains one verified daily ciphertext backup for
35 days and an additional pre-migration/pre-rotation copy until the replacement has
passed restoration and all dependent backups have expired or been re-encrypted.
The service owner must document any deployment-specific retention and recovery
objectives; these reference intervals are policy defaults, not measured guarantees.
Legal holds and application requirements may require a longer interval.

For each backup, record its digest, store ID, key dependencies, source version and
capture time. Capture with writers quiesced or an explicitly consistent snapshot
mechanism. Keep ciphertext and escrow under distinct access controls. Protect and
archive authority restore receipts separately; never roll the live trusted authority
back with the application backup. Follow the generation-checked
[authorized restore](storage-rollback-amendment-2026-09-10.md), which republishes old
backup data under current writer authority and advances the trusted generation.

Run a fresh-process recovery drill before each key retirement and at least monthly.
Compare restored application data with an independent expected result and retain
non-secret evidence. Expire a backup only after confirming no hold or recovery need
remains. Retire an escrow key only after inventory shows no retained ciphertext
needs it, current data has passed new-key-only recovery, and the reviewer records
that decision. Deletion from a filesystem is not proof of secure erasure from
snapshots, replicas, storage media or external custody; follow that system's actual
destruction procedure and record its result.

## Lost keys and failed recovery

If the live key is missing, stop repeated application writes and preserve ciphertext,
authority and receipts. Recover the exact key from escrow into a new private file,
verify the store ID and configuration, and test from a fresh process before resuming.
Missing/wrong keys and corrupt evidence must fail closed, including after a prior
cache hit. Never generate a replacement random key over the lost key's path and
interpret an empty database as recovery. Do not disable strict mode or authority to
force access.

If the key is unavailable from every authorized custody source, ciphertext that
requires it cannot be restored through this runtime. Preserve evidence and follow
the incident/data-loss process; there is no master key or plaintext fallback. A new
empty service is a separate, explicitly approved data-loss operation with a new
store identity, not a successful restoration. If ciphertext is corrupt but a verified
backup and necessary keys remain, use authorized restore with the current expected
generation and a recorded reason. Failure near publication is ambiguous: retain all
keys and evidence, inspect status and retry only the same reconciled operation.

## Repository evidence

The lifecycle drill and its pinned results are retained under
`validation/key-lifecycle/`. They exercise missing live credentials, escrow recovery,
current-key-only fresh reads, old-backup restoration under current writer authority,
and fail-closed loss of live key material, without retaining secret values. Existing
migration, writer-fence and rollback qualification covers interrupted publication;
live HTTP rotation is exercised with the frozen concurrent protocol and independent
offline outcome verifier. Finite tests do not establish universal correctness or
independent security certification.
