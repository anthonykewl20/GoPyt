# File I/O deadline checkpoint validation

Runtime fingerprint: `8954ed5f0c219bcc3342118ef7842d3548bc8e2d07e27761ce7aa704330912d3`.

The compiled file-write probe starts with `data.txt` containing `before`, gives
the task a 100 ms deadline, and injects a 300 ms delay before the resolved parent
directory descriptor is yielded. Before this change the call returns timeout at
300.87414 ms but replaces the contents with `after` (`before.py`, `before.json`).
Afterward it returns timeout at 300.650022 ms and preserves `before` (`after.py`,
`after.json`). Only the output path differs between probe scripts. This proves
that an already-expired context cannot begin the final file write after directory
resolution; it does not demonstrate interruption of the injected blocking call
or a 100 ms physical latency bound. The delay is controlled fault injection,
not a measured filesystem workload.

Six new compiled regressions cover expiry before file creation/truncation,
cancellation after opening with descriptor cleanup, complete short raw reads and
writes, cancellation after a partial write, cancellation after the first read,
allocation trap preservation and no-progress I/O errors. The partial-write case
requires the written prefix to remain, with no subsequent write call. Existing
path/symlink/hard-link and VM tests remain in the focused suite.

Both initial broader runs failed their auth example test because its toolchain
lock had not yet been refreshed (`initial-vm-lock-failed.log`,
`initial-focused-lock-failed.log`). All eight example locks now match; C030 keeps
its intentionally invalid package digest. The generated policy transaction lock
was checked for an active holder and removed before full validation. The final
125 focused tests pass on Python 3.14.7 and 3.11.16. Guard calibration (17),
stdlib parity (21 modules), reproducible builds, installed smoke and upgrade/rollback
also pass. Two builds at epoch 1788998400 produce identical wheel SHA256
`e2a2a4a5581d5dd4a1148b6622eabe0e23e16300cb16baddb3ce5461305c381c`
with 46 runtime files. The full Python 3.14.7 suite passes all 837 tests in
357.046 seconds. The contracts demo also passes.

The [normative policy](../../docs/parallel-admission-amendment-2026-09-10.md#file-io-checkpoints)
specifies 65,536-byte raw operation requests and inherited context checks.
Individual filesystem calls remain noninterruptible. Creation/truncation already
entered and partial writes may take effect despite a later timeout. No atomic
replacement, rollback, fsync or power-loss guarantee is introduced. This does not
qualify all native I/O, aggregate process memory, fairness or sustained resource
bounds; issue #13 remains open.

`source-reference.json` records the CPython FileIO reference used to preserve
short-I/O and nonprogress semantics. No dependency was added, and no upstream
source was copied or executed.

## CI publication-test correction

The first PR CI run on Ubuntu/Python 3.11 failed in the diagnostic audit's replay
of `test_parallel_timeout_waits_for_admitted_publication_then_traps`. The test
assumed its store could reach replacement within a 20 ms parallel deadline;
the new prepublication checks correctly rejected it earlier, so replacement was
never observed. The complete job log is retained as
`ci-ubuntu311-admission-failed.log` (run 34396866829, job 102618642568).

The revised test freezes the monotonic-nanosecond deadline clock until the
replacement hook is entered, then advances it beyond the actual inherited
deadline and waits for the real coordinator stop event. Wall-time waits and
joins remain real. It still requires timeout, a committed atomic outcome and
receipt reconciliation, without assuming a 20 ms admission latency. This test
correction changes no runtime code or production deadline check. The revised
18-test transaction/storage deadline suite passes on both Python versions. The
fresh full Python 3.14.7 run passes all 837 tests in 325.503 seconds, and contracts
pass again (`corrected-*` logs). Runtime/build inputs are unchanged, so the
previous wheel/install checks still apply; CI must qualify the new commit.
