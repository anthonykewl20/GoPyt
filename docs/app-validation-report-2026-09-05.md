# GoPyT first application: results and review — 2026-09-05

## Assessment

GoPyT **0.1.000** now runs a small, persistent support-ticket HTTP API with
atomic versioned updates. The application, independent acceptance tools,
benchmarks, source snapshots, bytecode artifacts and installable wheel are in
this workspace. This is the first application milestone; the quotation service
and integration worker have not been implemented in this campaign.

The app exposed two limitations in the original v0 scope, three HTTP defects,
an inefficient application algorithm, and a test-isolation issue. These were
addressed. Final suites pass **460 tests on Linux/Python 3.11.15 and 3.14.6**.
The unchanged external acceptance contract passes **170 cases**, with a further
**43 independent adversarial checks** and a **540-case identifier oracle**.

The optimized fixed-dataset read benchmark improved from **23.98 to 144.66
correct requests/second** with one client on this host. All five optimized
100-request/second arrival windows completed without drops or incorrect replies.
400-request/second arrivals still exceeded measured capacity. Storage cost grows
with database size, high-concurrency tail latency remains variable, and server
RSS continued to rise slowly during the two-minute soaks. This is a working
alpha application and a measured baseline, not production certification.

## Scope and independent work

The [plan](app-validation-plan.md) and [acceptance contract](../examples/tickets/README.md)
define create, retrieve, and `open → in_progress → closed` transitions. The
implementation is GoPyT source; Python is used for the compiler/VM and external
clients. The app has no authentication, listing/search, deletion, attachments,
notifications, custom HTTP status mapping, or multi-key transactions.

The root agent implemented the app and HTTP repairs. Separate subagents owned
storage, acceptance/measurement, and independent runtime/application review.
Acceptance clients inspect actual HTTP responses from real CLI subprocesses.
They do not use application functions as the expected-result oracle. The
identifier oracle uses the independently specified character set and length
rules; it passed against the original app before the optimization.

No Ranex dependency was added. This campaign uses independent test authorship,
frozen contracts, exact source/artifact identities, and retained raw results.
It is not a cryptographically authorized or tamper-proof grading environment;
the agents share filesystem authority.

## Findings and changes

| Finding | Evidence and action |
|---|---|
| V0 storage was deliberately process-local | A second VM could not retrieve a saved value. Added package-local durable SQLite snapshots, safe descriptor-relative access, atomic replacement and fsync. This explicitly amends the old process-memory specification. |
| Get followed by put is not a transaction | Competing readers could both accept a stale version. Added one primitive, `store.db.compare_exchange`, and used it for create/update. Threads and separate processes now produce exactly one winner for the same expected value. |
| Idle and drip-fed HTTP clients retained workers indefinitely | Real sockets reproduced worker starvation. Added an absolute ten-second deadline for request-line/header/body input, reset per request. Queued connections precede this deadline; application execution has no new total deadline. |
| Truncated Content-Length bodies reached handlers | A complete JSON document shorter than its declared body length could execute. Such requests now receive 400 and close before dispatch. |
| Small persistent HTTP responses stalled on TCP buffering | Three alternating-order controlled pairs reproduced approximately 41–42 ms medians with Nagle enabled, versus 0.36–0.42 ms with TCP_NODELAY. The shipped server enables TCP_NODELAY for all connections. This is an isolated transport result, not a whole-app speedup. |
| App identifier validation repeated an alphabet scan | Profiling showed validation consumed almost all measured handler work, dominated by repeated VM heap bookkeeping. Replaced slicing/looping over the alphabet with equivalent fixed character comparisons. No new language feature was necessary. |
| Example tests depended on a pristine working directory | A local demo could leave ignored state/cache files that failed tests or contaminated copies. Test staging now excludes generated state/cache files, and canonical checks inspect only application source trees. A real fixture-isolation test includes ignored candidate source files and replaces the assertion that an ignored build directory must never exist. |

The normative details are in the [storage amendment](storage-amendment-2026-09-05.md)
and [HTTP amendment](http-input-amendment-2026-09-05.md). The source compiler and
bytecode VM remain Python-hosted; no native compiler backend was added.

The app optimization changed only the private `id_char` function and its lock
digest. The public spec, README acceptance contract, language tests and manifest
are byte-identical in the archived before/after app snapshots. Compiler, VM,
storage and server runtime bytes were identical across the two HTTP campaigns.
Only test code changed in the compiler-tree inventory; the benchmark/probe
measurement implementations stayed unchanged. A new memory diagnostic was added
between campaigns and is identified separately in the inventories.

## Benchmark methodology

Full details and primary-source references are in
[benchmark-methodology.md](benchmark-methodology.md). The executed campaigns used:

- Five independent fresh-process repetitions per scenario, with a reproducible
  shuffled scenario order within each repetition.
- Two seconds of application warmup and five-second arrival/measurement windows;
  capped write trials can finish earlier. Each campaign also ran a 120-second
  closed-loop read soak with 16 clients.
- A fixed 64-record dataset (`bench-0` through `bench-63`, short titles), seeded
  outside timing, with deterministic read selection. Maximum legal payloads are
  acceptance-tested but are not this throughput workload.
- Closed-loop concurrency 1, 4, 16 and 64. Open-loop offered rates 25, 100 and
  400 requests/second, with 16 generator workers and 32 queued requests.
- Real HTTP/1.1 persistent connections, exact response validation, retained
  per-request timestamps/errors/drop reasons, and integrity checks after trials.
- Separate process-cold check and service-startup measurements. `run` compiles;
  OS filesystem caches were not flushed. Warmup uses one client, so higher
  concurrency includes worker ramp-up and first-use allocations.
- Server/generator CPU and server RSS sampled from `/proc` every 100 ms. Sampling
  can miss peaks. These are process metrics, not VM-cell counts.

Both campaigns ran sequentially on the same 16-logical-CPU Linux host using
Python 3.14.6. Other services remained active. We stopped our other load jobs;
we did not claim exclusive host access or change global CPU tuning. Environment
records retain CPU information, affinity, load averages and available cgroup
information. The sequential before/after order and shared host can confound
precise effect estimates; the large measured change is also supported by the
separate handler profile. No cross-language speed comparison is claimed.

Independent review recomputed every trial's counts, unique request indices,
throughput, nearest-rank latency percentiles, and the final group means/sample
standard deviations from the raw JSONL records. No discrepancy was found.

## Fixed-dataset HTTP reads

Values are mean correct responses/second **± sample standard deviation across
five trials**. They are not confidence intervals. Throughput includes in-flight
drain; closed-loop load is response-paced.

| Concurrent clients | Original app | Optimized app |
|---|---:|---:|
| 1 | 23.98 ± 0.70 | 144.66 ± 6.33 |
| 4 | 23.17 ± 0.49 | 122.40 ± 8.70 |
| 16 | 16.61 ± 0.67 | 121.46 ± 4.05 |
| 64 | 15.09 ± 1.29 | 104.68 ± 5.57 |

Single-client p99 latency ranged **51.94–64.19 ms before** and **10.31–13.41 ms
after** across trials. At 64 clients, optimized p99 ranged **979–3,237 ms**.
Higher concurrency is not automatically higher throughput in this interpreter.
The full per-scenario p50/p95/p99 and maximum values remain in the raw reports.

Optimized median service startup, including compilation and the first validated
response, was **140.3 ms**;
median separate `check` subprocess duration was
**129.6 ms**.
These are different measurements, not quantities to subtract for pure compile
or execution time.

## Offered load and durable creates

These are totals across five five-second arrival windows, including drain.
Drops occur at the **load generator's bounded admission queue**; they are not
HTTP 503 responses. Every dispatched response was correct in both campaigns.

| Offered rate | Original successful / offered | Original drops | Optimized successful / offered | Optimized drops |
|---|---:|---:|---:|---:|
| 25/s | 625 / 625 | 0 | 625 / 625 | 0 |
| 100/s | 626 / 2,500 | 1,874 | 2,500 / 2,500 | 0 |
| 400/s | 616 / 10,000 | 9,384 | 3,231 / 10,000 | 6,769 |

A short arrival window that eventually drains does not establish sustainable
capacity. Percentiles are conditional on dispatched attempts; read them beside
drops/errors. Offered-time latency includes generator scheduling and queueing.
For goodput inside the configured window, use `successful_by_deadline / 5`;
the actual-elapsed throughput field can slightly exceed a low offered rate
because the final scheduled arrival precedes the five-second endpoint.

Four-client durable create throughput was **17.56 ± 0.58/s before** and
**95.99 ± 1.68/s after**. This is supplementary: original trials wrote 86–93
records over approximately five seconds; optimized trials reached the 300-write
cap in 3.08–3.22 seconds. Their datasets grew by different amounts. These numbers
do not demonstrate a storage-engine speedup; the storage implementation was
unchanged between campaigns. Fixed-dataset reads are the cleaner comparison.

## Soaks, retention and storage scaling

| 120-second read soak | Original | Optimized |
|---|---:|---:|
| Correct responses | 1,974 | 14,616 |
| Incorrect dispatched responses | 0 | 0 |
| Throughput including drain | 16.38/s | 121.77/s |
| Initial sampled RSS | 28.61 MiB | 28.74 MiB |
| Final sampled RSS | 35.73 MiB | 35.44 MiB |
| Peak sampled RSS | 35.73 MiB | 36.10 MiB |

RSS growth slowed but remained positive in the second minute, approximately
7 KiB/s in each campaign. **Do not interpret this as flat memory or a resolved
leak.** Thread warmup, allocator retention and actual retained allocations need
longer controlled investigation. Equal-duration soaks did unequal work.

A separate direct-VM diagnostic performed **10,000 verified reads** in ten
batches. Post-collection tracked cells returned to **139 after every batch**,
and all 64 stored records remained correct. This constrains VM registry
retention for this fixed workload; it excludes HTTP threads, uses explicit GC,
and does not establish stable total process memory. Its RSS includes the
harness. `/proc` RSS and `getrusage` high-water counters disagreed slightly on
this host; they remain separately labeled rather than combined into a false
peak bound.

A separate storage-only sweep used one small target plus synthetic padding,
three fresh-process repetitions per size, 25 reads and five durable conditional
writes per repetition. Medians below are medians of the three trial medians.
Seeding/warmup are outside timing; writes include fsync.

| Padding | Read median | Conditional-write median | Peak sampled operation-window RSS |
|---|---:|---:|---:|
| 0 MiB | 0.171 ms | 1.518 ms | 24.36 MiB |
| 1 MiB | 2.055 ms | 5.638 ms | 28.29 MiB |
| 8 MiB | 15.704 ms | 28.740 ms | 50.29 MiB |
| 32 MiB | 64.911 ms | 137.974 ms | 90.44 MiB |

This exposes whole-snapshot copying/deserialization cost, even for a small
requested value. It is not HTTP throughput or a representative record-count
benchmark. The largest process-lifetime high-water reading was 186.41 MiB,
including untimed seeding; it is not the operation-window sampled peak. The
64 MiB database cap does not cap total process RSS.

## Final validation and evidence

- Final full suites: 460 tests each on Python 3.11.15 (90.051 s) and 3.14.6
  (77.151 s), with no failures or skips, after the final fixture-isolation correction.
- New regression coverage: 14 storage tests, 10 real-wire HTTP tests, and two
  ticket example tests including the 540-case oracle. Existing conformance,
  mutation/fuzz, VM, diagnostic, evolution and security suites remain included.
- External acceptance: 170 cases per final campaign, including real process
  restart, 16-way races and two server processes sharing the same store.
- Independent adversarial probe: 43 checks, including corrupt SQLite, 11 invalid
  stored records through three endpoints, state-transition races and validation
  precedence. All pass against the optimized app.
- Both archived app sources rebuild to the **exact bytecode hashes measured**.
  The final installable wheel passed isolated installation, fmt/check/test/run,
  real HTTP creation and persistence across a process restart.
- Stdlib synchronization: all 20 modules agree. Compileall and local Markdown
  link checks pass. No Git commit/PR or remote CI run was performed; this
  supplied workspace has no Git checkout.

The original full-suite logs remain alongside the final logs; earlier evidence
was not rewritten to imply later fixes existed. A test-fixture-only correction
followed timing; compiler/VM/app runtime bytes did not change, and rebuilt
bytecode still matches the measured artifacts exactly.

Evidence entry points:

- [Original campaign](app-evidence/2026-09-05/tickets-baseline/summary.json)
- [Optimized campaign](app-evidence/2026-09-05/tickets-optimized/summary.json)
- [Final external acceptance](app-evidence/2026-09-05/tickets-optimized/acceptance/acceptance.json)
- [Final independent adversarial checks](app-evidence/2026-09-05/tickets-adversarial-final/adversarial.json)
- [Storage sweep](app-evidence/2026-09-05/storage-size-sweep/summary.json)
- [VM retention diagnostic](app-evidence/2026-09-05/tickets-memory/memory-summary.json)
- [Profile before](app-evidence/2026-09-05/tickets-profile-before/profile-summary.json)
- [Profile after](app-evidence/2026-09-05/tickets-profile-after/profile-summary.json)
- [Evidence file manifest](app-evidence/2026-09-05/evidence-manifest.json)
- [Final Python 3.11 suite](app-evidence/2026-09-05/tests-final-verified-python-3.11.txt)
- [Final Python 3.14 suite](app-evidence/2026-09-05/tests-final-verified-python-3.14.txt)
- [Final wheel verification](app-evidence/2026-09-05/wheel-install-final.txt)

## Next steps justified by these results

1. Investigate remaining HTTP process-memory growth with longer controlled
   soaks and allocation attribution. Preserve the distinction between bounded
   VM roots and Python/SQLite/thread retention.
2. Profile and reduce VM heap bookkeeping at instruction boundaries without
   weakening root tracking, cancellation, or concurrent collection. The handler
   profile identifies this cost; it does not justify replacing the VM wholesale.
3. Improve storage read scaling behind the existing API, beginning with a
   carefully bounded snapshot cache that revalidates file identity under the
   same process lock. Test cross-process freshness, failed-write rollback and
   fork behavior before claiming an improvement. Whole-file write cost remains
   a separate backend problem; do not bypass confinement with unchecked paths.
4. Build the quotation-service workload next, with independent arithmetic and
   business-rule oracles. Add language/stdlib features only for demonstrated
   needs. Follow with the integration worker and real upstream-failure probes.

Ranex integration, a native backend, richer HTTP status mapping, search and
multi-key transactions remain separate decisions. None is required to explain
or hide the measured weaknesses. Linux/macOS CI, multi-day operation, power-loss
survival, hostile same-user file mutation, distributed storage and public
Internet deployment were not certified by this campaign.

## Run and reproduce

From the repository root, start the local demonstration:

```bash
cd examples/tickets
../../scripts/gopyt run tickets.serve
```

From another terminal:

```bash
curl -s http://127.0.0.1:8080/tickets \
  -H 'Content-Type: application/json' \
  -d '{"id":"first","title":"Try GoPyT"}'
```

Data persists under the package's `.gopyt-state` directory. Use a disposable
copy for experiments; local test staging excludes generated data/cache files.
The runtime address can be changed with `GOPYT_HTTP_ADDR`.

To repeat validation and measurements from the repository root:

```bash
python -m unittest discover -s gopyt -t . -p 'test_*.py'
python tools/ticket_probe.py --output /tmp/tickets-acceptance
python tools/ticket_adversarial.py --output /tmp/tickets-adversarial
python tools/ticket_benchmark.py --repeats 5 --seconds 5 --warmup 2 \
  --soak-seconds 120 --output /tmp/tickets-benchmark
python tools/storage_benchmark.py --output /tmp/tickets-storage
python tools/ticket_memory_probe.py --output /tmp/tickets-memory
```

Run timing commands sequentially after other load jobs finish. To reproduce the
original app, pass `--source docs/app-evidence/2026-09-05/tickets-baseline-source`
to the ticket benchmark. Retain the new run's environment and source identities;
these local results are not universal performance promises.
