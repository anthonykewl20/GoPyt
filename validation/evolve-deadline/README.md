# Evolution deadline validation

The compiled probe gives evolution a 100 ms VM deadline and replaces preparation
with a child delayed by 500 ms. Before the change, timeout returns after
768.436402 ms, after that child finishes. Afterward it returns after 149.340085 ms,
with the child stopped, no extra live children, no in-flight flag and unchanged
source. These are single controlled observations, not a scheduling SLO. Probe
scripts differ only in their output filename; reports retain script, fixture and
runtime identities.

Nine new regressions cover cancellation/deadlines after actual child startup,
partial frames, SIGTERM-resistant children, own-wave timeout versus VM timeout,
strict framed JSON, partial process startup, source-lock waiting, cancellation
during revalidation, and completion of entered recovery/commit. A setup trial
incorrectly patched locally imported fcntl through the transaction module; its
failure is retained. The corrected 161-test focused suites pass on Python 3.14.7
and 3.11.16. Guard calibration passes 17 tests and stdlib parity checks 21 modules.
Two wheel builds are bitwise identical with 46 runtime files; exact packaging
inputs and hashes are retained in build-report.json.

The complete Python 3.14.7 regression suite passes all 849 tests in 316.027
seconds. The contracts example, installed-wheel checks and compiler/runtime
upgrade and rollback checks also pass. Source, test and build files remained
frozen throughout the full run.

The [normative policy](../../docs/parallel-admission-amendment-2026-09-10.md#evolution-wave-deadlines-and-cleanup)
defines cooperative checks and cleanup barriers. Spawn startup, serialization,
filesystem/compiler calls and reaping may exceed the deadline. Interrupted
preparation may leave candidate caches. Entered commits may publish before timeout
is reported. This increment does not prove aggregate memory/cache limits, fairness,
sustained cancellation-storm leak bounds or complete architecture issue #13.

The pinned CPython reference informed socket transfer and process cleanup only;
no donor implementation was copied or executed.
