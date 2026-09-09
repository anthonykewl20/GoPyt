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
