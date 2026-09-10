# Operational metrics and what telemetry may carry

This document defines the metrics an operator can rely on, the rule that keeps
telemetry bounded, and what may and may not appear in it. It changes no GoPyT
source syntax and no closed standard-library signature.

It covers issue #21's metric definition and data-handling criteria. It does
**not** provide a source-level debugger or a profiling service; that remains
open, and no debugging protocol is registered by this runtime.

## The closed metric set

Every operational counter comes from a fixed table in `gopyt/observe.py`.
Counting a name outside it raises, rather than creating a metric. That is the
whole point: a metric keyed on a route argument, a principal, a key or a URL
would let one caller multiply the table, so the memory an operator pays for
telemetry would follow traffic. It does not. `memory_cells` is constant for the
life of a VM and a test asserts it does not move across two thousand events.

Counters are exact and saturate at 2^64 − 1 rather than wrapping. The Count-Min
and Bloom structures remain internal: they estimate tag frequency and are never
presented as exact operator metrics.

| Family | Counters | What it tells an operator |
|---|---|---|
| `deny:` | `resource`, `secret`, `limit`, `egress`, `rate`, `database`, `identity`, `service_token`, `gateway`, `forwarded_identity` | Authorization and authority refusals, one family across every surface, so they can be counted together rather than inferred from HTTP status mix |
| `alloc:` | `refused_bytes`, `refused_descriptors`, `refused_mapped`, `refused_handles`, `overload_trap` | Which configured budget actually refused work, recorded at the definitive refusal after the managed heap sweep |
| `queue:` | `connection_rejected`, `worker_refused`, `request_expired` | Pressure at the serving edge: a full accept queue, worker admission refusal, and a request that lost its deadline while queued and so never reached a handler |
| `conflict:` | `compare_exchange`, `writer_fence`, `snapshot_generation` | Concurrency outcomes a caller is expected to retry, kept apart from errors |
| `contract:` | `precondition`, `postcondition` | Contract failures, counted apart from other traps: an obligation being violated is not an ordinary runtime error |

`Observe.metrics()` returns those counters together with the event and failure
totals, the latency mean and variance, the anomaly alarm and `memory_cells`. A
failed count never replaces the outcome it describes: the trap or denial is
raised or returned regardless.

## What telemetry may carry

Two kinds of text reach this module, and they are not equivalent.

**Compiler-derived identifiers.** Task and trap names come from the compiled
artifact, so they are declared identifiers rather than request data. These are
the only names that appear in a retained failure sample, whose rows carry
exactly `task`, `tag` and `code` — never arguments, returned values, request or
response bodies, keys, or headers. A name longer than 256 bytes is replaced by
its SHA-256 digest.

**One application-supplied string.** `core.observe.note` takes a tag from
application code. It updates the frequency sketches and nothing else: it is
recorded with a non-failure outcome, so it can never enter the failure-sample
reservoir, and it is not recoverable from anything this module emits. A test
drives a secret through it and asserts it appears in no output.

Request logging is off. Automatic snapshots retain bounded task, tag and code
samples only.

**What this does not redact.** An application that calls `core.log.write` or
reveals a secret is making its own choice, and this document does not override
it. Redaction here covers the runtime's own telemetry, not what application code
deliberately emits.

## Bounded overhead

Each event performs a fixed number of sketch updates and at most one exact
counter increment under one lock, so per-event cost does not depend on how many
distinct tags have been seen. The retained structures are the Count-Min table,
the Bloom filter, the bounded reservoir, the scalar moments and one slot per
closed metric name.

## Not covered

No source-mapped debugger, no profiling service, and no access-controlled
diagnostic protocol exists. Diagnosis of real workload failures and performance
regressions under telemetry loss and overload has not been qualified. Both
remain open under #21.
