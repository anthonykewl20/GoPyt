# Ticket runtime follow-up — 5 September 2026

## Assessment

This follow-up keeps the ticket application and bytecode unchanged and improves
three runtime details: heap admission overhead, repeated storage reads, and HTTP
handler cleanup. All **477 regression tests pass on Linux/Python 3.11 and 3.14**;
the **170 external acceptance cases** and **43 adversarial checks** also pass.
The installed wheel preserves ticket data across a real server restart.

The read cache produces a clear benefit for a bounded, repeatedly read working
set. Cold reads and durable writes still have whole-snapshot costs. HTTP results
are noisier: one-client throughput averaged **135.77 ± 7.75 versus 154.14 ± 28.23
correct requests/second**, with five trials per runtime. Shared-host variation
and high-concurrency tail latency remain substantial. These observations do not
establish production capacity or a universal speedup.

An independent real-HTTP regression confirms that finished handlers no longer
depend on Python cyclic GC for reclamation. The instrumented memory diagnostic
kept VM cells and worker counts stable but did not explain all RSS retention.
The ten-minute read soak completed **91,595 correct replies with zero errors**.
RSS rose from 28.51 to 32.01 MiB, including a late 128 KiB step after several
minutes at the same sampled level. Broader memory stability remains unproven.

Release remains **GoPyT 0.1.000**, Python package metadata **0.1.0**, and bytecode
format **2**. No new language syntax, stdlib signature or ticket contract was
introduced. The quotation application remains the next application milestone.

## Changes and independent review

| Finding | Change and verification |
|---|---|
| Every instruction repeatedly allocated traversal lists and reacquired the heap RLock for existing roots | A private lock-held admission helper returns early for scalars and already registered cells. Instruction locks, collection thresholds, admission order, handoffs and released-native-call rules remain intact. Six independently authored boundary tests pass against both original and final collectors. |
| Every storage read loaded and deserialized the entire database | Each Store keeps at most 256 validated results and 1 MiB of UTF-8 key/value payload. Every hit still opens the database safely under flock and checks its metadata fingerprint. Ten new regressions cover invalidation, corruption, bounds, failures, threads and fork recovery. |
| The request deadline callback captured its own HTTP handler | A numeric deadline now lives on the reader. This removes the reference cycle while preserving per-request deadline resets. With cyclic GC disabled, six real HTTP requests leave no live finished handlers. The frozen baseline fails this independently authored test; the candidate passes. |

Root implemented the heap and HTTP changes. Separate agents owned storage,
memory attribution and independent review. Reviewers and implementation agents
share filesystem access; their work is not a cryptographically isolated audit.
The [plan](runtime-followup-plan-2026-09-05.md) records the sequence and the
[cache amendment](storage-cache-amendment-2026-09-05.md) specifies the limits.

The cache retains immutable strings or absence, with no SQLite connections or
file descriptors. Its payload limit excludes Python object/container overhead
and applies per Store. Hits validate device, inode, size, nanosecond mtime and
ctime; writes, detected file changes and failures within the locked operation
invalidate retained results. A timeout acquiring the Store mutex does not access
the cache; the next successful operation still revalidates the file. Writes
always reload disk. Metadata fingerprints provide freshness for cooperating
writers on suitable local POSIX filesystems, not content authentication against
a hostile owner. Arbitrary fork during active file I/O remains unsupported.

The source snapshots separate the original, heap-only, heap-plus-cache, and
final runtimes. Only `heap.py`, `storage.py`, and `server.py` changed among runtime
Python modules. The application rebuilds to the same measured artifact hash in
all three handler profiles:
`95a179a3fa67e3af84bbef137d205d9a4844c26138e39476aac5910ac7e9b988`.

## Measurement protocol and limits

Timing jobs ran sequentially after this task's test and diagnostic load stopped.
Other services on the shared 16-logical-CPU host remained active. Python 3.14.6
ran the measurements; each trial retains its environment and exact source
identities. The earlier application report remains unchanged: its measurements
were taken under different host conditions and are not the control for this run.

The HTTP comparison used five before/after pairs for each of 1, 16 and 64
clients: **30 fresh-process trials**. Pair order alternated AB/BA across
repetitions, and concurrency order was shuffled with a fixed seed. Each trial
seeded the same 64 records outside timing, warmed for two seconds and measured
five seconds of closed-loop reads using the original exact-response driver.
OS caches were not flushed; warmup used one client. High concurrency therefore
includes additional worker activation. Throughput includes in-flight drain.

The runner freezes its own hash and the runtime/application/driver identities,
checking them throughout. Raw records retain timestamps, status and correctness
flags; successful response bodies are not retained. Independent postprocessing
can recompute counters and statistics from those records, but cannot reconstruct
the successful HTTP bodies. The live driver validates complete expected replies,
while the separate acceptance tools retain expected and actual responses.

Means and sample standard deviations below summarize separate fresh-process trials. They
are not confidence intervals. Closed-loop loads are response-paced; this run
does not measure sustainable offered-rate capacity or repeat the earlier
open-loop overload campaign. Pair order reduces time-order bias but cannot
remove unrelated CPU, scheduler, allocator and filesystem interference.

## HTTP reads

All **16,796 replies** in the 30 paired trials were correct, and final state
integrity checks passed in every trial.

| Clients | Baseline correct requests/s | Candidate correct requests/s | Baseline p99 range | Candidate p99 range |
|---|---:|---:|---:|---:|
| 1 | 135.77 ± 7.75 | 154.14 ± 28.23 | 11.00–12.30 ms | 8.95–20.05 ms |
| 16 | 87.48 ± 30.96 | 98.87 ± 43.89 | 410.33–616.28 ms | 157.26–446.97 ms |
| 64 | 77.75 ± 30.98 | 106.71 ± 49.83 | 984.78–2,242.42 ms | 749.51–3,336.98 ms |

Candidate mean throughput is higher in these samples, but the changes did not
remove latency variability. Its worst observed single-client and 64-client p99
values are higher than the corresponding baseline extremes. Do not present
these averages as a guaranteed throughput or tail-latency improvement.

Separate direct-handler profiles provide mechanism evidence. Thirty instrumented
`tickets.retrieve` calls took 1.471 seconds in the baseline, 1.141 in the
heap-only snapshot and 1.157 in the final snapshot. The repeated public `adopt`
work disappears from the instruction path; VM dispatch and heap bookkeeping
remain substantial costs. These profiler totals include instrumentation and
exclude HTTP. The three small unprofiled controls are diagnostics, not an
independent speed comparison.

## Storage hits, misses and writes

The cache-aware diagnostic used three fresh-process repetitions at each of
0, 8 and 32 MiB padding. Each repetition alternated the order of 25 warmed
repeated-key reads and 25 reads through fresh Store instances over the same
database. Store construction occurred outside timing. "Fresh Store" means an
empty application cache, not a cold disk or OS page cache.

Values below are medians of the three trial medians, in milliseconds:

| Padding | Baseline warmed | Candidate warmed | Baseline fresh Store | Candidate fresh Store |
|---|---:|---:|---:|---:|
| 0 MiB | 0.236 | 0.054 | 0.248 | 0.165 |
| 8 MiB | 26.408 | 0.114 | 25.279 | 21.335 |
| 32 MiB | 112.110 | 0.121 | 111.875 | 84.798 |

Each runtime completed **450 verified timed reads** and **45 correctness-only
external-process updates**. Every external update was visible through the
already warmed Store on its next read. Versions ran sequentially; differences
in miss timings should not be attributed to a new storage algorithm. The
same-process hot/miss contrast establishes the cache's narrower benefit.

The unchanged storage-size sweep also ran on both runtimes at 0, 1, 8 and
32 MiB, three repetitions per size, with 25 reads and five durable conditional
writes. Final-runtime write medians were **1.98, 7.43, 36.23 and 165.06 ms**
respectively. Writes still load, serialize and durably replace whole snapshots;
their observed speed differences are not a demonstrated write-engine improvement.
The database cap remains 64 MiB, not a process-RSS cap.

The size sweep batches hot reads together; the cache-aware probe interleaves
them with expensive misses. Their different hot-read timings reflect different
measurement conditions. A small repeatedly read key does not represent random
access to a working set larger than the cache or write-heavy traffic.

## Memory attribution and extended soak

The [detailed memory investigation](app-evidence/2026-09-05-followup/memory-diagnostic/attribution.md)
retains raw before/after-GC snapshots and allocation-site comparisons. Each
runtime completed **2,704 real HTTP operations**, including 2,560 read-phase
requests. It retained **142 post-GC VM cells and 66 Python threads** at every
checkpoint. HTTP execution has additional server and argument roots; its count
and the earlier direct-only probe's 139 cells describe different execution
contexts, rather than an allocation-for-allocation retention comparison.

With 64 warmed connections closed, explicit Python GC reclaimed 832 objects
in the baseline and zero in the candidate. Closing 16 read connections produced
208 versus zero. The independent weak-reference regression establishes the
handler-cycle defect and its removal. Aggregate GC counters do not establish
that every reclaimed object belonged to that handler cycle.

Most connection buffers already freed on close before Python collection. The
baseline Python-GC stages reduced traced bytes by approximately **137 KB and
40 KB**, not the entire 8.5 MB open-to-closed reduction. The matched candidate
ended with higher absolute RSS despite smaller growth during its read phase.
Neither observation supports a general memory-reduction or no-leak claim.

Tracing and explicit collection perturb allocation and speed. One eight-frame
pilot was aborted and preserved; completed runs used one frame. Idle controls
matched snapshot count over five seconds, not read-phase duration. Root's final
regression suites overlapped part of these fixed-work diagnostics; all timing
benchmarks started after that load stopped. Native allocation, allocator
capacity/fragmentation and tracer overhead remain possible explanations for
RSS not accounted for by retained traced bytes, rather than established causes.

| Final-runtime ten-minute soak | Observation |
|---|---:|
| Correct replies / errors | 91,595 / 0 |
| Elapsed including drain | 600.017 s |
| Correct requests/second | 152.65 |
| Request p99 | 147.90 ms |
| Initial RSS after warmup | 28.51 MiB |
| Final / peak sampled RSS | 32.01 / 32.01 MiB |
| Server threads in all resource samples | 65 |

The [derived time-series summary](app-evidence/2026-09-05-followup/soak-analysis.json)
shows RSS unchanged at 31.88 MiB from the second through seventh minute. During
the eighth minute it increased by **128 KiB**, then stayed at 32.01 MiB through
the end. The last-five-minute OLS slope is approximately **0.626 KiB/s**, driven
by that step; it is not evidence of a continuous leak at that rate. Sampling at
100 ms can miss peaks. The plateau and late step are both retained in the report.

The ten-minute soak is final-runtime only, with no tracing or explicit GC added
by the harness. It is an extended observation of the fixed read workload, not
a paired memory comparison, mixed-workload endurance test or proof of leak
freedom. Final RSS is sampled after load connections close; the intermediate
time series is needed to assess the active-load trend.

## Final verification and next work

- Full suite: **477 tests**, no failures or skips, on Python 3.11.15
  (**199.015 s**) and 3.14.6 (**175.378 s**). Earlier 476-test logs are retained
  separately and are not described as final-cycle verification.
- New coverage: ten storage regressions, six collector boundary tests and the
  HTTP handler lifetime regression. Existing 540 identifier subcases and all
  compiler/VM, fuzz, evolution and security suites remain included.
- External acceptance: 170 cases. Independent adversarial checks: 43. Clean
  wheel installation: exact packaged Python bytes, fmt/check/test/run and real
  HTTP creation/retrieval across process restart.
- Stdlib synchronization: all 20 modules agree. Local compileall and document
  link checks pass. The language/toolchain release label and bytecode version
  remain unchanged. No Git commit, PR, remote CI or macOS run was performed.
- Independent recomputation passed for **108,391 recorded correct HTTP requests**
  across the paired trials and soak, both storage-size campaigns and both
  cache-aware campaigns. Raw counts, unique indices, timing arithmetic,
  nearest-rank percentiles, group statistics, resource metrics and recorded
  source hashes reconcile. Recorded correctness flags are not a replacement
  for independently retained response bodies.

The next application should exercise a different part of the language: a small
quotation service with exact arithmetic and independent business-rule oracles.
Keep the ticket workload as a regression benchmark. Further runtime work should
target measured high-concurrency scheduling/heap contention, cold-read and write
scaling, and memory behavior under connection churn and mixed traffic. The
current cache is useful within its stated bounds; it does not justify declaring
those wider problems solved or introducing a native backend or Ranex dependency.

Evidence entry points:

- [HTTP comparison](app-evidence/2026-09-05-followup/http-comparison/comparison-summary.json)
- [Cache-aware storage baseline](app-evidence/2026-09-05-followup/storage-cache-baseline/summary.json)
- [Cache-aware storage candidate](app-evidence/2026-09-05-followup/storage-cache-candidate/summary.json)
- [Storage-size sweep](app-evidence/2026-09-05-followup/storage-candidate/summary.json)
- [Memory attribution](app-evidence/2026-09-05-followup/memory-diagnostic/attribution.md)
- [Independent review](app-evidence/2026-09-05-followup/independent-review.md)
- [Independent recomputation](app-evidence/2026-09-05-followup/independent-recomputation.json)
- [Evidence manifest](app-evidence/2026-09-05-followup/evidence-manifest.json)
- [Final Python 3.11 tests](app-evidence/2026-09-05-followup/tests-final-python-3.11.txt)
- [Final Python 3.14 tests](app-evidence/2026-09-05-followup/tests-final-python-3.14.txt)
- [Final wheel installation](app-evidence/2026-09-05-followup/wheel-install-final.txt)

To repeat the comparison into a new empty directory:

```bash
python tools/runtime_comparison.py \
  --baseline-root docs/app-evidence/2026-09-05-followup/baseline-source \
  --candidate-root docs/app-evidence/2026-09-05-followup/candidate-source \
  --output /tmp/gopyt-runtime-comparison \
  --repeats 5 --clients 1 16 64 --seconds 5 --warmup 2 --soak-seconds 600
```

The retained [measurement sequence](app-evidence/2026-09-05-followup/run-measurements.py)
records profile and storage commands as well. Use new output directories when
reproducing; preserve historical evidence. Run instrumented memory diagnostics
separately from timing jobs.
