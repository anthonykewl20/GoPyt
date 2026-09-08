# GoPyT application validation plan

This work begins with one complete support-ticket API. The quotation service
and integration worker remain subsequent workloads; findings from this first
application determine whether new language features are justified.

## Responsibilities and order

1. Freeze the HTTP acceptance contract in `examples/tickets/README.md` before
   measuring. Root implements the app; a separate agent writes external checks.
2. Reproduce storage and runtime blockers. Add the smallest specified fixes,
   including regressions. A separate reviewer probes HTTP and concurrency.
3. Run canonical formatting, checking, language tests, independent HTTP
   acceptance, process restart, and races against a copied package.
4. Review the benchmark driver before execution. Separate compile/startup,
   warmed requests, persistent writes, and a sustained read workload.
5. Run repeated fresh-process trials. Retain all outcomes, environment and
   source/artifact identities, per-request measurements, errors, process RSS
   and CPU, and load-generator scheduling delays. Do not equate live VM cells
   with operating-system memory or successful requests with offered requests.
6. Re-run the complete compiler/runtime suite after fixes, then review the
   final application, storage, server, and measurement code independently.
7. Publish measured results, remaining limitations, and a prioritized feature
   backlog. No benchmark numbers are release gates invented after observing
   the results. Correctness acceptance remains mandatory.

## Acceptance and interpretation

The app must preserve acknowledged writes across a normal restart and abrupt
process termination; reject duplicate creation and stale updates atomically;
reject illegal state transitions; and retain correct behavior after malformed
requests and bounded concurrent load. Checks inspect real HTTP responses and
state after restart, not implementation internals as the expected answer.

Benchmarks run on the current host and identify its actual interpreter and
environment. They establish a local baseline, not a hardware-independent SLA
or a comparative claim against other languages. Throughput tests use declared
closed-loop concurrency; scheduled-rate tests additionally report offered-time
latency and generator drops to expose coordinated omission. Independent trials
are the unit of replication; requests inside a trial are not independent runs.

No finite test campaign establishes exhaustive correctness over all programs,
operating systems, inputs, process schedules, or power failures. Unexecuted
platforms and hardware durability scenarios are reported explicitly.
