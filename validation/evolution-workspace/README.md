# Evolution proposal workspace cleanup

Public proposals now own private staging directories under
`evolve/.work-<random-id>/<package-digest>/`. The parent reaps preparation children,
finishes admitted apply/recovery work, and removes only its own workspace before
returning. Concurrent proposals do not share a candidate tree. Cleanup uses the
held parent-directory descriptor, rejects a replaced workspace root, and does
not follow descendant symlinks. The
[normative ownership policy](../../docs/parallel-admission-amendment-2026-09-10.md#evolution-proposal-staging-ownership)
defines cleanup failures, cancellation and commit ambiguity.

## Repeated-wave evidence

Run `PYTHONPATH=. python validation/evolution-workspace/retention-probe.py` from
the repository root. It operates on an isolated copy of the auth package,
restores the baseline limiter before each of five distinct valid revisions, and
runs the real spawned preparation/apply path with two candidates and a ten-second
wave budget. Source/probe/runtime identities and each package digest are retained.

Before the change (`before.json`, runtime fc6c1aa5), all five waves applied and
retained one additional digest tree each: 14, 28, 42, 56 and 70 files, occupying
11,809 through 59,045 bytes. After the change (`after.json`, runtime a9c68e8f), the
same probe bytes produce the same Applied output digests and retain zero candidate
trees/files/bytes after each wave. The persistent weights record is deliberately
outside these candidate-tree counts. This is a five-wave filesystem observation,
not a staging-byte quota or production stress/latency qualification.

## Regression and packaging qualification

Six new tests cover real repeated-wave cleanup while preserving legacy evidence,
partial preparation error/cancellation cleanup, concurrent ownership, descendant
symlinks and replaced roots, preservation of primary cancellation on cleanup
failure, and cleanup failure after a real source apply. Existing spawned-worker
deadline tests now write partial candidate data inside staging and assert it is
removed after cancellation, timeout, partial frames, startup failures and forced
child termination. They also retain their child/socket/in-flight cleanup checks.

All 73 focused tests pass on Python 3.14.7 (7.243 s) and 3.11.16 (7.774 s). The
full Python 3.14.7 suite passes 880 tests in 339.257 s with runtime/test sources
frozen throughout. The branch includes PR60's separately qualified idle-client
regression correction before this full run. Guard calibration (17 tests), stdlib
parity (21 modules), contracts, installed-wheel checks and upgrade/rollback pass.
Two wheel builds are bitwise equal with 46 runtime files; wheel SHA-256 is
`a5af92a5b38d069b795623f092b3c2bbd9f1505742bc27d26ac4da81947c0121`.
All 49 packaging-input hashes were verified unchanged after the full run.

Runtime SHA-256:
`a9c68e8f882d9928dedf039330832856fe4d74ea0f133ec0a80457ce73b4b7fa`.
`source-review.json` binds the regression test sources and the pinned CPython
shutil source inspected for fd-relative removal; no donor code was copied or
executed and no dependency was added. `build-report.json` records build inputs.
The fresh deadline inventory pins the qualified runtime commit and can be checked
with `python tools/check_deadline_inventory.py validation/evolution-workspace/deadline-inventory.json`.
Historical inventories remain unchanged.

## Failure semantics and remaining limits

Injected cleanup failure after apply returns EvolveError while leaving the
committed source/lock change intact. Existing cancellation/traps stay primary if
cleanup also fails. Such failures may leave staging files and require
reconciliation; there is no false successful-cleanup report or rollback claim.

Older caches and operator evidence are not swept. Internal prepare/apply helpers
called separately retain host-owned plan lifetimes. Abrupt parent death and
filesystem cleanup failure can leave orphan directories; automatic orphan
recovery is not implemented. Removal may exceed the elapsed deadline because
filesystem calls are synchronous. Active package-copy size, aggregate memory,
legacy/orphan disk quotas and issue #13's remaining sustained workload and
resource qualification are separate. The passing tests establish the stated
ownership paths, not universal cleanup under hostile host/filesystem mutation.
