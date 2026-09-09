# Shared native admission deadline validation

Runtime fingerprint: `97a6fcaee2c687f5eda9e1cc27ddeac93139a5a5a793553a0a163d713b163403`.

The compiled limiter probe holds `vm.lock` for 300 ms while its task has a
100 ms deadline. Before this change it returns timeout after 300.265812 ms and
has consumed the sole token (`before.py`, `before.json`). Afterward it returns
timeout after 100.276321 ms with no bucket created (`after.py`, `after.json`).
Only the output path differs between probe scripts. These are finite diagnostic
observations, not a production latency distribution or fairness measurement.

Three new regression methods compile limiter, evolution and HTTP serving entry
points. They hold the actual shared lock until the caller returns timeout or
cancellation, then require unchanged limiter buckets, evolution cooldown/in-flight
state and serving state. Spies verify that rejected calls never start evolution
preparation or listener-address setup. Other cases deliver cancellation/deadline
at acquisition and inject an exception inside the critical section, checking
that the lock is released and later admission succeeds.

The first broader commands named a nonexistent `gopyt.test_scheduling` module;
the other 35 selected tests passed, but each command failed module loading.
Those command errors are retained (`initial-command314-failed.log`,
`initial-command311-failed.log`). The corrected 48-test focused suites pass on
Python 3.14.7 and 3.11.16. The initial full run passes all 840 tests in 325.657
seconds (`initial-full314.log`). Guard calibration (17), stdlib parity (21 modules),
reproducible builds, installed smoke and upgrade/rollback also pass. Two builds
at epoch 1788998400 match wheel SHA256
`6318f6a8c4160de5b62cc98e2945d3142ca617a045c1fd0f5b3532da8c99685c`
with 46 runtime files. This command correction bypassed no runtime validation.

PR #51 CI separately exposed the existing 20 ms publication-admission test
assumption. Its deterministic clock correction is also included here; see the
[retained diagnosis](../file-deadline/README.md#ci-publication-test-correction).
The fresh full run with that correction passes all 840 tests in 325.307
seconds (`full314.log`); the contracts demo also passes. Runtime source remains
the fingerprint above.

The [normative policy](../../docs/parallel-admission-amendment-2026-09-10.md#shared-native-admission-lock)
defines bounded polling and a post-acquisition context check. Limiter time is
sampled after admission. An already admitted operation may change state before
later cancellation is observed; no rollback or FIFO guarantee is introduced.
This increment does not change other heap/observation locks, admitted evolution
worker waits or cleanup barriers. Broader native/resource and sustained stress
requirements keep issue #13 open.

`source-reference.json` reuses the reviewed CPython timed-lock reference. No new
dependency, source copying or donor execution was needed.
