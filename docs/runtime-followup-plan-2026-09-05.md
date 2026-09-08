# Ticket workload: runtime follow-up plan

Continue the first application's measured weaknesses before expanding its
features. Preserve the ticket contract and bytecode; optimize runtime details
only when independent correctness probes and measurements support them.

## Changes and responsibilities

- Root: remove redundant heap admission work while retaining instruction locks,
  root admission, handoffs, collection thresholds and released-native-call rules.
- Storage agent: bounded validated read-result cache, preserving descriptor-based
  confinement, cross-process freshness, durability and failed-write behavior.
- Memory agent: instrument a separate real HTTP server process to distinguish
  Python allocations, GoPyT roots, worker growth and RSS, with idle controls.
- Root and independent reviewer: eliminate the deadline callback's confirmed
  reference cycle through the HTTP handler; verify finished handlers are released
  without depending on Python cyclic GC. Request deadline semantics stay fixed.
- Independent reviewer: boundary/concurrency tests and review of the changes,
  diagnostic methodology and final evidence. Shared filesystem access is not an
  adversarial isolation boundary.

## Evidence and acceptance fixed before measurement

The original runtime, application and tools are archived in
`app-evidence/2026-09-05-followup/baseline-source`, with a file hash manifest.
A heap-only snapshot separates that optimization from storage changes. Existing
evidence from the first campaign remains unchanged.

Correctness gates remain exact responses and persisted state, independent
acceptance/adversarial tests, collection during concurrent calls, cache misses,
cross-process updates, invalid database replacement, failed writes and bounded
cache resources. Run the complete regression suite on Python 3.11 and 3.14 after
review fixes. Do not invent a timing threshold after observing the measurements.

Timing jobs run sequentially, after test and diagnostic load stops. Use fresh
processes, fixed 64-record datasets and the existing exact-response HTTP driver.
Run five before/after pairs for closed-loop concurrency 1, 16 and 64, alternating
which runtime goes first within each pair. Each trial has two seconds of warmup
and a five-second measured window. Preserve all per-request records, drops,
errors, percentiles, server/generator metrics and source hashes. Report means
and sample standard deviations over trials, not over individual requests.

Repeat the existing storage-only size sweep against original and final runtimes:
0, 1, 8 and 32 MiB padding; three fresh-process repetitions per size; 25 reads and
five durable conditional writes. This repeated-key workload primarily measures
cache hits after warmup. Add a separate cache-miss/working-set diagnostic if
necessary; do not generalize hits into faster arbitrary database access or writes.

Use direct handler profiles to attribute the heap change, separate from HTTP
timing. Use instrumented HTTP memory diagnostics with workload and idle phases,
pre/post-GC snapshots and exact replies. Instrumentation changes speed and memory;
it cannot establish uninstrumented throughput or prove leak freedom. Extend the
uninstrumented final-runtime read soak to ten minutes to test whether the earlier
two-minute RSS trend persists. Preserve time series and phase definitions.

All measurements are shared-host exploratory evidence. The alternating order
reduces time-order bias but does not control unrelated services, CPU scheduling,
allocator variation or filesystem caching. Do not claim cross-language speed,
production capacity, universal bounded memory or correctness over all schedules.

The first eight-frame tracing run was stopped as an excessively intrusive pilot,
with partial evidence retained. The matched attribution runs use one trace frame,
64 seeded records, 64 warmed worker connections, 16 read clients, eight batches
of 20 reads per client, and five-second idle controls. These are fixed-work
diagnostics with explicit GC checkpoints; their durations are not benchmarks.

## Completion

Record measured effects and remaining limits in a new report, retaining the
original report unchanged. The quotation application follows this investigation;
its feature needs require a separate contract and independent business-rule
oracle. Release remains 0.1.000 and bytecode format remains 2.
