# Storage writer-key fencing qualification

The committed pre-change design and `before.py` / `before314.json` reproduce a stale
writer republishing under a retired key after rekey. The repaired authority profile
binds the permitted publishing key to the trusted generation record. See the
[normative amendment](../../docs/storage-writer-fence-amendment-2026-09-10.md).

## Results

- Full Python 3.14.7 regression: 910 tests passed in 341.996 seconds (`full314.log`).
- Focused storage, rollback, fencing, key configuration and deadline regression:
  81 tests passed on Python 3.11.16 and 3.14.7 (`focused311.log`, `focused314.log`).
- Eight new fencing tests include an actual waiting process with stale loaded
  settings; stale put, no-op conditional writes, rekey and restore; explicit v1
  migration; key-label independence; CLI/schema rejection; and process exits before
  authority rename, before snapshot rename and after snapshot rename.
- The independent functional probe passed on both pinned Pythons. It checks exact
  writer identity and generation, unchanged bytes after a rejected stale write,
  independently decrypts the restored ciphertext and queries its SQLite rows,
  reads from a fresh process with only the new key, and verifies the preceding
  qualified v1 runtime rejects v2 authority metadata. Probe source and all input
  hashes are retained in `oracle.py` and `oracle-qualified*/`.
- Guard 17 tests, stdlib parity, contract example, installed wheel, and installed
  compiler/runtime upgrade and rollback passed (corresponding logs).
- Two wheels were bitwise equal: SHA-256
  `430dff9dd0fc46ed57377851e030a253b61e0737eb05905580e717a4df410459`.
  `build-report.json` records 50 packaging inputs, 47 runtime files, pinned build
  versions and epoch 1788998400. Build and probe input hashes were rechecked after
  the full regression, before retaining this evidence.

## Unsuccessful trials and limits

`oracle314/`, its log and `oracle-initial.py` retain a probe setup failure: the child
inherited another checkout as its working directory. `oracle-final311/` and
`oracle-final314/`, their logs and `oracle-second.py` retain the subsequent setup
failure: zip-importing the old wheel cannot provide the real source files required
by its toolchain fingerprint. The qualified probe sets the child working directory
and extracts the hash-verified old wheel into a private directory. Neither correction
changed runtime sources. Earlier passing focused runs are also retained.

These are functional Linux checks and process-crash checks, not power-loss,
production latency, hardware custody or independent security-audit certification.
The old v1 wheel is identified by SHA-256 in the probe; it is extracted for the
compatibility check only. The fresh-process retirement test retains old keys for
backup authentication in the trusted maintenance process, while the new reader has
only the new key. No secret key bytes are retained in results.

## Issue #11 coverage

This increment covers stale writers, interrupted transitions, fresh-process key
retirement and explicit authority v1-to-v2 transition. Ciphertext remains SIV1;
plaintext migration and broader provisioning/custody/lost-key/backup lifecycle
requirements remain open. No language native grants writer authority. Existing
unanchored and v1 profiles still require quiesced maintenance. All writers must use
the trusted authority profile; this is not filesystem isolation from a compromised
host or protection against rollback of the trusted authority itself.

Implementation reference: CPython commit
`823f0323ee6ec1402088b73bce1a38473cac36dc`, `Lib/tempfile.py` private exclusive file
creation and `Lib/shutil.py` descriptor-safe removal were inspected as references
for the surrounding authority work; no donor code or dependency was adopted here.
The key identifier follows the repository's existing SHA-256 cache identity pattern.
