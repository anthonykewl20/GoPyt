# Runtime resource stress

The protocol was committed as `e72c337` before harness development and observations. It fixes five scenarios, runtime capacities, minimum duration/count, and acceptance budgets. Commit the workload sources before running the measured campaign:

```sh
python3 validation/runtime-resource-stress/run.py --output /tmp/new-campaign
python3 validation/runtime-resource-stress/verify.py /tmp/new-campaign
```

Linux `/proc` is required. The saturation case opens 1,088 client connections against 64 workers and a 1,024-entry queue, so the process needs an adequate descriptor limit. `--smoke` runs two episodes per phase and does not qualify sustained behavior. Measured runs use three warmups, then at least 30 episodes and 60 seconds per phase. Lock episodes alternate a held local lock and a held process lock; the latter also queues callers behind the local lock.

`inputs.json` freezes runtime, fixture and harness hashes, Git identity, Python/platform, limits and protocol. `episodes.jsonl` retains measured facts, elapsed time, and resources before/after explicit garbage collection. `report.json` reports per-phase baselines, final counts, sampled peaks and episode latency percentiles. A failed episode is retained before its oracle is checked; exceptions retain a traceback and failed report. Warmups are validated but excluded from measured episode counts and latency summaries. `execution.log` retains fixture diagnostics.

The oracle imports no runtime code: it checks literal HTTP responses, independently read SQLite state, admission/cancellation counts and cleanup. Five corrupted functional observations and one extra descriptor must be rejected. `verify.py` rechecks retained observations offline; it does not attest that observations were honestly collected. Harness source inspection and hashes remain necessary.

Resource budgets apply after collection and quiescence, relative to each phase baseline. Sampling includes synchronous observations at controlled capacity/publication points, but peaks can occur between samples. Episode latency includes compilation, setup, coordination and cleanup; it is not a request-latency SLO. These controlled tests do not establish aggregate memory bounds, crash durability, hostile-provider isolation or a security audit.

Development trials and measured results are retained separately. No acceptance budget may be loosened in response to a failed observation without declaring a new protocol and rerunning qualification.

## Completed measured campaigns

Both Linux runs used committed workload `ab7be78` and the same runtime identity recorded above in `inputs.json`. Each phase exceeded 60 seconds after three warmups. All functional checks, six negative controls per run and post-GC resource budgets passed; offline verification passed on both pinned interpreters.

| Python | Phase | Episodes | Seconds | p99 episode ms | Maximum episode ms | Final RSS growth bytes |
|---|---|---:|---:|---:|---:|---:|
| 3.14.7 | slow_clients | 176 | 60.116 | 345.633 | 361.875 | 77824 |
| 3.14.7 | saturation | 211 | 60.098 | 308.121 | 345.370 | 643072 |
| 3.14.7 | cancellation_storm | 598 | 60.048 | 101.672 | 123.421 | 1474560 |
| 3.14.7 | lock_contention | 713 | 60.339 | 145.766 | 418.675 | 90112 |
| 3.14.7 | shutdown_write | 223 | 60.188 | 483.494 | 563.844 | 8192 |
| 3.11.16 | slow_clients | 176 | 60.269 | 346.469 | 357.038 | 81920 |
| 3.11.16 | saturation | 203 | 60.220 | 382.475 | 569.911 | 1269760 |
| 3.11.16 | cancellation_storm | 569 | 60.061 | 147.015 | 177.599 | 724992 |
| 3.11.16 | lock_contention | 644 | 60.105 | 196.385 | 967.395 | 36864 |
| 3.11.16 | shutdown_write | 224 | 60.244 | 384.475 | 551.243 | 102400 |

Every measured post-GC descriptor/thread/child count met the zero-growth budget relative to its phase baseline. RSS growth is relative to post-warmup phase baselines, not process startup. Full raw facts and source identities are in [measured314](measured314/) and [measured311](measured311/); [development](development/) retains unsuccessful setup trials and successful smoke runs. No measured trial failed. These measurements qualify this controlled Linux workload, not macOS stress behavior or deployment SLOs.
