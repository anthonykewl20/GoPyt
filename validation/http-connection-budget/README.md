# Connection occupancy budgets

With one worker and a two-request test quota, the pre-change persistent client
holds the worker after its second response while another accepted connection is
queued. The queued client is served only after peer closure. After the change,
the final response advertises `Connection: close` and the queued client is served
before that peer closes. The probe waits for actual queue admission. Scripts
differ only in report filename; runtime and script identities are retained.

Five new wire-level regressions cover quota release, no dispatch of excess
pipelined requests, unchanged absolute connection deadline across requests,
idle lifetime release and non-handler responses counting toward quota. The first
idle test gave near-simultaneous clients equal one-second lifetimes; the queued
client also expired. That failed trial is retained as evidence of the policy's
lack of guaranteed service. The corrected slot-release test gives the queued
client a separately captured longer lifetime, leaving the old connection's
one-second lifetime unchanged. All 45 focused tests pass on Python 3.14.7
(11.277 s) and 3.11.16 (11.505 s).

The prerequisite worker-entry regression correction is incorporated; every
connection-runtime source hash is unchanged from focused/build qualification.
Guard calibration, stdlib parity, reproducible wheels and installed-wheel/upgrade
checks pass. All 865 full regression tests pass on Python 3.14.7 in 307.919 seconds;
the contracts example also passes. Runtime, test and build sources remained
frozen throughout the full run. The wheel report pins all packaging inputs;
two builds are bitwise identical with 46 runtime files.

The [policy](../../docs/parallel-admission-amendment-2026-09-10.md#connection-occupancy-and-queue-fairness)
defines a ten-second connection lifetime and 100 request attempts. It provides
finite cooperative occupancy and FIFO connection admission, not strict request
fairness or a guarantee of service before expiry. Noninterruptible work and
admitted commit cleanup can outlast expiry. Closure does not roll back effects
or automatically replay requests. These are bounded regression observations,
not sustained production fairness, latency or resource qualification.

The refreshed [deadline inventory](deadline-inventory.json) pins this runtime.
Run `python tools/check_deadline_inventory.py validation/http-connection-budget/deadline-inventory.json`.
Earlier inventories remain immutable reviews of their respective runtimes.
