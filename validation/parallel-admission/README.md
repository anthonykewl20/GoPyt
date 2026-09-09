# Parallel admission and deadline validation

Runtime SHA256: `8872ca111baee3227ab1711bac21c612fa6feec9b3b870d3a57c490e9cc69e21`.

All 808 regression tests pass on Python 3.14.7 in 311.032 seconds.
The 28 focused scheduling/time/native tests pass on Python 3.14.7 and 3.11.16.
Guard calibration (17), stdlib parity (21 modules), contracts demo, installed
wheel smoke and upgrade/rollback pass. Two isolated builds at epoch 1788998400
produce wheel SHA256 `9793b65acce0133b757afd0459eadf4ae42fdcece61908ee9f9686d1ef617325`.
Logs and the build report are retained here.

## Frozen workload and measured correction

`before-nested.json` retains a pre-change compiled reproduction: three nested
`parallel max 4` levels reached 64 simultaneous sleeping native calls. Local
limits worked; there was no aggregate VM reservation.

Reproduce the current stress with `python tools/parallel_admission_probe.py --output /tmp/new-admission.json`.
The tool freezes runtime/tool/workload identities before running and checks them
afterward. It runs 100 sequential 5 ms timeouts around 200 ms sleeps, then 256
attempts from eight host drivers against a VM with eight worker slots. Host-driver
threads are outside the VM worker budget. The oracle checks exact trap classes,
capacity bounds, cleanup counts and a subsequent successful call. Latencies and
RSS are observations, with no production SLO or universal leak claim.

The initial trial (`initial-stress.json`) passed its declared cleanup checks but
exposed a wait that ignored the shorter inherited deadline: sequential p99 was
54.892312 ms. Both sleep loops were then corrected to clamp waits to the remaining
shared budget. The in-progress old full suite was deliberately interrupted to
apply that fix; `initial-full-interrupted.log` is incomplete, not a passing suite.
The completed final full suite is `full314.log`.

The final unchanged workload (`stress.json`) reports sequential p50 5.674432 ms,
p99/max 8.811533 ms; the concurrent phase has 6 timeouts and 250 overloads, with
maximum observed latency 26.009761 ms. Peak reserved workers are eight. Idle
worker slots, frames, pins and handoffs return to zero; live threads remain 1,
descriptors remain 5, and language heap objects remain 10. RSS increases from
29,188,096 to 30,257,152 bytes. This short Linux trial does not establish native
memory leak freedom or an OS latency bound. The earlier trial is retained to
make the tuning history explicit; its runtime fingerprint differs.

## Scope remaining

The normative [amendment](../../docs/parallel-admission-amendment-2026-09-10.md)
defines fail-fast admission, inherited deadlines, HTTP 503/504 and join-before-return
including thread-start failure. Regression tests include a noncooperative writer
that finishes before a timeout can return, demonstrating commit ambiguity.

Issue #13 remains open: uniform blocking-I/O budgets, whole-request deadlines,
graceful draining, broader queue/buffer/retry qualification and sustained shutdown,
cancellation and leak evidence are not completed by this increment. CPython
thread lifecycle reference identity and license-file hash are retained separately;
no reference code was copied or executed.
