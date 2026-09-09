# Storage rollback authority design

Proposed design for issue #12; this document does not claim an implemented control.
The retained [baseline probe](../validation/storage-rollback-design/before.json)
shows that a fresh Store accepts an older authenticated ciphertext after a newer
write. The key and stable storage context are unchanged. Encryption authenticates
content, not freshness.

## Threat and trust boundary

The protected adversary can copy, replace, delete or restore application snapshot
files and their package-local metadata, including valid old encrypted snapshots.
It cannot change the operator's configured store identity, access storage keys,
modify the trusted authority state, or roll that authority back with the package.
A compromised runtime/host or simultaneous restoration of the authority is outside
this control. Filesystem permissions alone do not isolate hostile code executing
as the runtime user; the OS-isolation issue remains necessary for that profile.

The initial authority should be an operator-provisioned, private directory outside
application and backup roots, on storage excluded from application rollback. It
provides durable compare-and-advance records under a process lock. Each store has
an operator-assigned identity and a monotonically increasing generation, bound to
the exact ciphertext digest. A package-local sidecar, timestamp, nonce or cached
process counter cannot replace this authority. The provisioning workflow must
reject absent/malformed records during ordinary operation rather than silently
enrolling a replayed snapshot. A remote authority can implement the same contract
later; this proposal does not imply a remote service exists.

## Publication order and recovery

1. Acquire the store and authority locks in one documented order. Read the current
   authoritative generation and digest. Every read, including a cache hit, must
   validate authority freshness; every write must compare its expected generation.
2. Verify the current snapshot's ciphertext digest and authenticated content before
   use. Reject mismatches, missing snapshots and substituted store identities.
3. Prepare the next encrypted snapshot in a uniquely named, fsynced staging file.
   Bind its ciphertext digest and next generation into the trusted authority record.
4. Durably advance the authority before replacing the active snapshot. Once this
   advance begins, complete admitted publication/recovery before observing ordinary
   cancellation, preserving the existing commit-ambiguity contract.
5. Atomically replace and fsync the snapshot directory. Acknowledge only after both
   authority and snapshot durability barriers complete.

A crash before authority advancement leaves the previous snapshot authoritative.
A crash after advancement must never accept the previous generation as a fallback.
Recovery may publish only the exact authenticated staged ciphertext named by the
advanced record; missing or substituted staging fails closed. A crash after snapshot
replacement simply verifies the new digest. Staging cleanup must run after this
recovery decision, so the current generic pending-file sweep cannot delete the
only recoverable candidate first. Reusing a ciphertext during restore must still
advance generation, never reset it.

These steps require a concrete record format and migration/publication state machine
before implementation. The record must identify any recovery staging name safely,
reject ambiguous/duplicate fields and oversized values, and protect stable lock
identity against unlink/substitution. Authority failure must return a typed storage
failure without serving cached data or falling back to unanchored operation.
A timeout can coexist with an advanced authority or committed snapshot; receipts
and reconciliation remain necessary. No automatic application-operation replay is
introduced.

## Enrollment and authorized restoration

Enrollment is a distinct operator action with the application stopped. It validates
an existing authenticated snapshot (or an explicitly empty new store), creates a
new authority identity once, and durably records the initial generation/digest.
Ordinary runtime access cannot recreate a missing authority or downgrade protection.
Protection must be selected in trusted host configuration, not application files.

Authorized restore is another explicit operator action. It authenticates the selected
backup under the expected store context, requires the current authority generation,
and publishes the chosen older logical contents as a *new* generation. It records
the backup digest, old/new generations and an operator-supplied restoration reason
in trusted restoration evidence. It never restores the authority from the same
backup. Interruption uses the same authority-first recovery rule. Loss or rollback
of the authority requires a separate operator trust-reestablishment procedure and
must not be labeled verified rollback protection.

## Frozen functional acceptance cases

Before implementation, preserve the current replay probe and the expected outcomes:

- Old ciphertext replay rejects on fresh and cached reads and cannot be overwritten
  by an ordinary write; the legitimate current snapshot still succeeds.
- Package metadata, store identity, generation, digest and staging substitution
  reject. Missing/malformed/unavailable authority rejects without fallback.
- Concurrent processes cannot advance from the same generation twice.
- Crash injection at every staged-write, authority-write, replace and fsync boundary
  either keeps the old generation before admission, recovers exactly the admitted
  new snapshot, or fails closed when required evidence is unavailable.
- Acknowledged writes remain readable after restart; cancellation after authority
  advance does not manufacture a rollback claim.
- Explicit restore advances generation, retains restoration evidence, and makes
  pre-restore snapshots ineligible for ordinary replay. Failed/stale operator
  requests cannot silently override a newer generation.
- Enrollment cannot overwrite an existing authority; runtime operations cannot
  auto-enroll or downgrade; key rotation preserves the authority contract.

Tests must use real encrypted snapshots and separate processes where the contract
requires process serialization/crash behavior. An independent reader must inspect
record generations/digests and durable logical state. Retain failed trials. This
functional design makes no latency, availability percentage, recovery-time or
independent security-audit claim; those need separately selected targets and trials.
