# Ticket application acceptance and benchmarking

This milestone evaluates whether GoPyT can implement a small persistent HTTP
application correctly and measures its current behavior on a recorded machine.
It does not establish production readiness or a speed ranking against another
language. Complete correctness evidence before interpreting performance.

## Frozen acceptance contract

`examples/tickets/README.md` defines the observable application contract. The
external Python client in `tools/ticket_probe.py` checks that contract over real
HTTP against `python -m gopyt run tickets.serve`; it does not import application
handlers or use the application's tests as its oracle. The CLI runs in a copied
temporary package with fresh storage and a separately allocated loopback port.
Compiler, application, harness, and emitted bytecode SHA-256 identities are
recorded. A local port allocation race remains possible; a failed launch is a
failed trial, never a successful zero-request result.

Acceptance covers exact response fields and types, creation and retrieval,
duplicate IDs, valid and invalid identifier/title boundaries, Unicode titles,
malformed JSON and schema, integer boundaries, content type, unknown routes,
allowed and forbidden transitions, stale versions, missing records, sixteen-way
create and transition races both within one server and across two processes, a fixed-seed state-machine sequence, acknowledged
state after abrupt process termination and restart, and graceful shutdown.
The abrupt restart checks acknowledged durability, not power-loss survival.
Runtime-specific adversarial transport/storage tests supplement this app probe.

```bash
python tools/ticket_probe.py --output /tmp/gopyt-ticket-acceptance
```

A failed assertion exits unsuccessfully and retains `acceptance.json` with
expected and actual outcomes. Do not weaken acceptance expectations to obtain
passing results; change the explicit contract first when requirements change.

## Experimental design

The default measured matrix uses three fresh server processes per scenario,
three seconds per measurement, one second of explicit warmup, and a separate
sixty-second read soak. Scenarios run sequentially in a reproducible shuffled
order within each repetition. No other audit or benchmark jobs should run
concurrently during final measurements. All cases use persistent HTTP/1.1
connections. Each process has the same sixty-four prepopulated records, generated
outside timing; read selection uses a fixed seed. A supplementary four-client
create trial measures durable writes with distinct IDs and a maximum of three
hundred writes per trial. Its actual duration, count, and workload are reported
separately because its storage grows while the read dataset stays fixed.

The repetitions, warmup separation, and explicit host-noise caveats follow
[pyperf's benchmark guidance](https://pyperf.readthedocs.io/en/latest/run_benchmark.html).
This harness measures a whole HTTP subprocess service; it does not claim to use
the pyperf engine. Three short repetitions are an exploratory baseline. They
are insufficient for release performance guarantees or precise tail estimates.

Two workload models answer different questions:

- **Closed loop, 1/4/16/64 clients:** Each client sends its next request after the
  previous response. This measures behavior under response-paced concurrency.
  It cannot show latency for work that would have arrived during a stall.
- **Open loop, 25/100/400 offered requests per second:** Requests have scheduled
  arrival times independent of completions. Sixteen workers consume a bounded
  queue of thirty-two waiting requests. Every scheduled request gets a raw
  record, including queue-full and generator-late drops. Response latency is
  measured both from actual dispatch and from its scheduled arrival. Queueing
  and generator scheduling delays are therefore visible. Requests dropped
  before dispatch have no response latency and remain separate counts rather
  than disappearing from the offered workload.

Measuring from intended arrival time addresses the coordinated-omission
problem explained in the [wrk2 design](https://github.com/giltene/wrk2).
This Python generator is not wrk2 and has its own capacity limits. Inspect
its CPU usage, scheduling delay, errors, drops, and achieved throughput before
attributing overload to the server. An isolated load-generator machine is
needed for stronger service-capacity claims.

## Measurements and retained evidence

Each request retains its sequence number, planned/start/end times, dispatch and
offered latency, response status, correctness, or exception/drop reason in a
JSONL file. No response-body mismatches count as successful throughput. Trial
summaries include planned/dispatched/received/successful/error/drop counts; p50/p95/p99
and maximum response and offered latency; and scheduler-delay percentiles.
Percentiles include dispatched failures when timing is available, and are
explicitly conditional on dispatch: missing responses must be read alongside
their errors and dropped arrivals. Sparse samples make p99 estimates unstable;
inspect request counts and the raw data. No outlier is silently discarded.

The measurement window includes load-generator worker setup; setup overhead is
therefore included rather than silently subtracted. Throughput divides successful completions by actual elapsed time including
in-flight drain. Completions within the configured window are also recorded.
The report retains each independent trial's throughput and p99, plus mean,
sample standard deviation, and minimum/maximum throughput across repetitions.
It does not combine requests into a pseudo-independent confidence interval or
average per-request percentiles into a purported global percentile.

A separate `gopyt check` subprocess duration includes interpreter startup,
imports, parsing, checking, emission, validation, and output writes. CLI service
startup is measured from process launch to the first verified HTTP response;
`run` compiles again. Readiness polling resolution is approximately ten
milliseconds. These are process-cold measurements, not filesystem-cache-cold
measurements; no operating-system caches are flushed.

On Linux, server and generator user+system CPU time and server resident set size
come from `/proc/PID/stat`. RSS, CPU, and thread counts are sampled every 100 ms;
short RSS peaks can be missed. Baseline and final values are also retained.
Logical VM heap counts are not substituted for total process memory. On systems
without `/proc`, these measurements are explicitly null. Environment evidence
includes Python, operating system, logical CPUs, process affinity, load average,
CPU/memory information, cgroup CPU quota when present, and clock resolution.

```bash
python tools/ticket_benchmark.py --output /tmp/gopyt-ticket-baseline
```

The runner executes acceptance first, then the measurement matrix and soak.
Every measurement process receives a fresh copy of the application, and its
bytecode and source identities are recorded. `summary.json`, per-trial JSON,
and per-request JSONL files form the evidence set. Incorrect measured responses
make the command unsuccessful. Offered-load drops are reported without
pretending they are application-correctness failures or successful responses.

For a longer follow-up after the exploratory run:

```bash
python tools/ticket_benchmark.py --repeats 10 --seconds 30 --warmup 5 \
  --soak-seconds 600 --output /tmp/gopyt-ticket-extended
```

Freeze the compiler/app/harness revisions, record the machine configuration,
ensure other jobs are stopped, and compare raw trial distributions. Choose
additional offered rates around the observed saturation region. Only claim a
regression or improvement after repeated comparable runs with correctness
intact. This first milestone deliberately makes no unmeasured latency or
throughput promise.

## Separate profiling and retention diagnostics

Run attribution tools after HTTP measurements have stopped; they must not share
the machine with a measured trial. `tools/app_profile.py` invokes
`tickets.retrieve("bench-0")` and `tickets.valid_id("bench-0")` directly through
the VM after compilation and sixty-four-record seeding. It records cProfile
call graphs in raw pstats plus sorted self/cumulative-time JSON tables. Each
call checks the exact result and releases the VM result root. Compiler,
application, artifact, profiler, and helper hashes bind the diagnostic to the
code examined. Default thirty-call profiles and three unprofiled control calls
are diagnostic samples, not replacements for the repeated HTTP experiment.
Profiler overhead changes timings; corroborate attribution before optimizing.

```bash
python tools/app_profile.py --output /tmp/gopyt-ticket-profile
```

`tools/ticket_memory_probe.py` evaluates tracked-VM retention separately from
whole-process memory. After seeding the same sixty-four records, its default
workload makes ten batches of one thousand exact-checked direct VM reads of
`bench-0`. Every returned value is released from the VM root set. Each batch
records tracked heap cells and types, collection count, current process RSS,
and process high-water RSS before and after explicit VM collection. High-water
RSS includes compilation, seeding, and all prior phases. Current RSS uses
Linux `/proc/self/statm`; unsupported measurements remain null.

The logical-heap acceptance bound is declared before execution: no more than
256 additional tracked cells above the seeded post-collection baseline, for
this fixed workload. This is a bounded-retention probe, not a universal runtime
heap limit. All sixty-four records are verified again after the final batch.
No RSS threshold is used. RSS includes the harness and its evidence-serialization
allocations, while Python garbage collection runs normally; no explicit Python
`gc.collect()` is inserted. Explicit VM collection also changes normal execution.

```bash
python tools/ticket_memory_probe.py --output /tmp/gopyt-ticket-memory
```

Neither diagnostic exercises HTTP sockets, worker threads, or server queues.
Stable VM cell counts do not prove stable Python/SQLite/native allocator or HTTP
server memory, and cannot establish absence of a leak. A rising RSS soak remains
a separate unresolved observation until longer server runs and appropriate host
allocation/retention evidence explain it. Per-batch diagnostic durations are
retained for reproducibility and are not reported as benchmark throughput.
