# Plaintext storage migration qualification

The frozen design and baseline in this directory establish the missing initial
migration operation. The implementation adds explicit digest-checked migration with
an encrypted recovery copy committed before active replacement. Ordinary encrypted
reads continue to reject plaintext. See the normative migration amendment.

## Evidence

Full regression: 919 tests passed in 358.083 seconds (`full314.log`). The contract
example passed afterward (`contracts.log`). All 51 packaging inputs and both
125-file probe source inventories were verified unchanged after the full run.

`focused311.log` and `focused314.log` record 90 passing focused tests on Python
3.11.16 and 3.14.7. Nine migration tests cover the actual CLI/API, independent
ciphertext decryption and SQLite rows, retries, missing active-file recovery,
wrong digest/key/schema/values, unsafe recovery paths, incompatible authority
configuration, contended recovery-lock deadlines, and cancellation after recovery
copy admission. Twelve real spawned process crashes cover before/after recovery
link, active replacement and all four publication fsync barriers.

`oracle.py`, `oracle311/` and `oracle314/` retain separately captured runtime,
interpreter, source and probe identities. Each probe migrates 513 distinct Unicode
rows through the real CLI, independently decrypts using literal framing and AAD,
compares exact original plaintext bytes and all SQLite rows, rejects a wrong-digest
negative control without changes, and recovers a deleted active file from the
verified encrypted copy before a fresh process reads the last row. These are
functional checks, not a workload performance measurement.

The reproducible build contains 48 runtime files and records 51 packaging inputs.
Both wheels have SHA-256
`e07b96d161280dfbe993140cd48964ac9724e91bfbf899221d93285982bbcc49`.
`build-report.json` retains epoch 1788998400 and pinned build/runtime versions.
Guard, stdlib parity, installed-wheel and installed upgrade/rollback checks passed.

## Unsuccessful trials and scope

`before-initial.py` retains an initial baseline-probe setup error: an empty
`GOPYT_DB_POLICY_FILE` selects an invalid explicit policy rather than absence of
policy. That invocation failed before source creation. Its traceback was observed
in tool output; no separate original log artifact was saved. The committed
`before.py` clears inherited GoPyt settings and omits the policy variable. It
reproduces encrypted read/rekey rejection and the absent migrate command without
changing the plaintext snapshot. No failed migration-runtime trial was observed in
these qualification runs. Earlier focused passing runs are retained as such.

This operation requires quiesced writers and an unanchored initial store. Enroll
trusted version-2 authority after migration for fenced key changes. The recovery
copy must authenticate under the selected active key; retaining an old reader key
alone does not qualify it. Unknown ciphertext formats remain rejected. These tests
do not establish power-loss durability, secure erasure of historical plaintext,
production recovery objectives, hardware custody or independent security audit.
Issue #11 remains open for its remaining key-lifecycle requirements.
