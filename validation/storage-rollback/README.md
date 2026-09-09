# Trusted snapshot rollback and restore qualification

The runtime under review is
`b612d392140aaf04a9f93edd1e110cdc61cee08d563a806ea3dc094ab3202cba`.
The [protocol design](../../docs/storage-rollback-design.md) and
[old-runtime replay evidence](../storage-rollback-design/README.md) were committed
before implementation. The [normative amendment](../../docs/storage-rollback-amendment-2026-09-10.md)
defines operator configuration, generation and restoration semantics.

## Acceptance mapping

| Issue #12 criterion | Concrete evidence |
|---|---|
| Threat model and trusted monotonic state | Design/amendment distinguish attacker-controlled application snapshots from the operator-protected authority, configuration and keys. The authority must be excluded from application rollback. This is not full-host rollback protection or OS isolation. |
| Bind generations; define failure and availability | `rollback.py` serializes durable generation/digest changes under an authority lock. `storage.py` validates every ordinary read, including cache hits, and advances the authority before snapshot publication. Missing/mismatched authority or recovery evidence fails closed. |
| Authorized restoration and disaster recovery | Existing operator CLI gains explicit enroll/status/restore while preserving rekey. Restore authenticates the backup, compares the expected current generation, publishes fresh ciphertext at a new generation and retains trusted restoration receipts. Lost authority cannot silently fall back or auto-enroll. |
| Replay/substitution/crash/unavailability/restore tests | Eighteen new tests exercise real encrypted snapshots, compiled typed errors, fresh/cached replay, process competition, lock cancellation, private-file/link failures, missing authority/staging, restore CLI and receipt recovery. Spawned processes exit before/after six durability barriers and at three publication boundaries. These are process exits, not power-loss tests. |

## Retained checks

- `qualified-focused314.log` and `qualified-focused311.log`: 73 tests each, including
  storage, transaction, deadline, security, key-rotation and all new rollback tests.
- `complete314.log`: 18 new tests, including the process-crash subcases.
- `oracle314/` and `oracle311/`: exact source/probe/Python identities and independent
  state checks. The probe operates Store but decrypts with a literal format/AAD,
  reads SQLite rows independently, and checks the ciphertext digest and generations
  from authority JSON. A deliberately wrong expected state is rejected.
- `build-report.json`, `repro.log`, `wheel.log`, `upgrade.log`: matching reproducible
  wheels, 47 bundled runtime modules, installed entry points and compiler/runtime
  upgrade then rollback. Packaging input hashes cover 50 files.
- `guard.log`, `stdlib.log`: Guard calibration and standard-library parity.
- `full314.log`: all 898 full regression tests passed in 358.152 seconds.
  The 50 packaging input hashes and reviewed source/test hashes were rechecked
  unchanged after the full run. `contracts.log`: the runnable contracts example passed.

No throughput, latency percentile, production RTO/RPO or independent security-audit
claim is made. The before probe demonstrates acceptance of old ciphertext on the
unprotected baseline; the new probe demonstrates rejection with authority enabled
and deliberate restoration as a later generation. Their random keys/nonces differ,
so ciphertext equality across runs is not an expected outcome.

## Unsuccessful trials and corrections

The first operator CLI edit dropped the existing rekey subcommand. The existing
key-rotation test reproduced the regression (`cli-initial-failure.log` and
`initial-store-admin.py`). The revised interface retains the old command, JSON
results and error exit code; both final 73-test suites include its regression.

The first independent probe used an incorrect ten-byte header length for the
11-byte literal magic. Both runs failed before the first state observation;
`oracle-initial.py`, `oracle-initial314/`, `oracle-initial311/` and their logs retain
the failed setup and identities. The corrected probe derives the offset from its
own literal format, independently of GoPyt's decoder. No acceptance expectation
was loosened to make the result pass.

## Operational limits

Each authority directory serves one configured store identity and must be protected
from rollback, including its parent paths and backup policy. Filesystem mode checks
do not establish that deployment property. Authorized host compromise or restoration
of the authority can invalidate freshness, as specified in the threat model.

Hashing on cache hits scans the bounded snapshot. Generation exhaustion and any
missing authority or exact recovery candidate reject rather than degrading to
unprotected storage. OS filesystem/fsync calls may block; admitted publication or
recovery can outlive cancellation. A failed operation may have advanced or committed
state, so application receipts/reconciliation still matter.

Restoration receipts record admitted intent, not proof of response delivery.
Earlier receipts require operator archival; automatic retention is not supplied.
Abrupt process death can leave temporary authority files; only the authoritative
record and named snapshot candidate determine recovery. Power-loss behavior,
remote authority availability and production disaster-recovery budgets require
separate deployment qualification.
