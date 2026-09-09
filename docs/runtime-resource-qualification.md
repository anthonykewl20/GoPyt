# Cancellation, admission and resource ownership qualification

This review maps issue #13 to the shipped Python runtime. The runtime identity is
`a9c68e8f882d9928dedf039330832856fe4d74ea0f133ec0a80457ce73b4b7fa`,
qualified by the [proposal-workspace regression and build evidence](../validation/evolution-workspace/README.md).
The [native inventory](../validation/evolution-workspace/deadline-inventory.json)
identifies all shipped native groups and generated conversion boundaries. Optional
providers and host-injected natives retain the documented host ownership boundary.

| Criterion | Implementation and evidence |
|---|---|
| Deadline propagation, cancellation and ambiguity | [Deadline specification](deadline-boundaries.md), [parallel amendment](parallel-admission-amendment-2026-09-10.md), and [transaction outcomes](transaction-outcomes-amendment-2026-09-09.md) define inherited absolute budgets, cooperative delivery, structured joins and reconciliation after publication admission. |
| Queue, buffer, retry and fairness policy | [Capacity review](runtime-resource-budget-review.md) records finite fixed/configured capacities and ownership. Subsequent [JSON allocation enforcement](../validation/json-allocation-budget/README.md) and [proposal staging cleanup](../validation/evolution-workspace/README.md) repair its two identified gaps. HTTP pending connections are FIFO; parallel capacity rejects atomically; outbound transport does not retry or follow redirects. Neither scheduler promises starvation freedom. |
| Graceful shutdown and deterministic owned cleanup | [HTTP drain evidence](../validation/http-drain/README.md) covers normal signals, admitted durable writes and failed workers. [Startup cleanup](../validation/http-startup/README.md), [outbound transport](../validation/outbound-budget/README.md), [file cleanup](../validation/file-deadline/README.md), [storage admission](../validation/storage-deadline/README.md), and [proposal ownership](../validation/evolution-workspace/README.md) cover failure paths and descriptor/worker/child/staging release. |
| Slow clients, saturation, cancellation, contention and writes during shutdown | [Frozen stress campaign](../validation/runtime-resource-stress/README.md) implements all five scenarios, retaining raw observations, latency, post-GC resources, independent literal/state oracles and unsuccessful development trials. See that report for actual completed runs and limits. |

## Ownership review

The server closes its listener, discards queued requests, shuts down incomplete
connections and joins started workers. Normal drain permits an already admitted
handler to finish under its existing context; cancellation aborts sockets and
still joins. Handler and worker finalizers release heap handoffs. No further
pipelined request is admitted after drain begins.

Parallel reservations and result roots remain owned until all started workers
join, including partial startup failure. Outbound response/error paths close the
transport; file descriptors and store lock/directory/SQLite handles use finalizers.
Public evolution proposals own a private staging tree and remove it after child
reaping and admitted apply/recovery. Cleanup errors remain observable; a primary
failure is preserved with a cleanup note. These are release obligations, not a
promise of bounded OS close/join/fsync latency.

Durable replacement and source journal admission are the relevant commit
boundaries. A deadline does not undo an entered publication. The shutdown stress
oracle reads SQLite state independently of the Store reader and requires the
acknowledged first write while excluding the undispatched pipelined second write.
Existing transaction tests cover cancellation/commit ambiguity and reconciliation;
HTTP response loss still requires application receipts.

## Boundaries of the claim

Per-value allocation limits, encoded payload limits and finite configured queues
bound their named resources. They do not impose an aggregate object-graph/RSS quota
across arbitrary application values, VMs or host callers. JSON decoding materializes
an application result and may hold intermediate copies. Large legal observation
reservoir or candidate settings need deployment sizing. The ordinary JSON ceiling
is distinct from the smaller HTTP wire ceiling.

A blocked host log sink, DNS/filesystem call or trusted optional provider can delay
cancellation and joining. An absent optional provider is tested as a typed failure;
an installed provider needs its own qualification. Abrupt parent death can leave
proposal staging; legacy caches and persistent weights are not swept. Crash recovery,
foreign resource APIs, aggregate isolation and production deployment guarantees
remain in their respective architecture issues. Finite passing tests do not
establish universal correctness or security.
