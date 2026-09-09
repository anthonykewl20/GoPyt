# Deadline and cancellation boundary specification

This normative consolidation specifies architecture #13's first acceptance
criterion for the shipped Python VM. It complements the
[parallel/admission amendment](parallel-admission-amendment-2026-09-10.md), whose
per-operation budgets and admission checkpoints remain authoritative. It does
not establish the separate queue, fairness, shutdown or sustained stress criteria.

## Shared rule and precedence

A calling thread has an optional absolute monotonic nanosecond deadline and a
chain of cancellation events. A nested parallel group and each of its workers
inherit the minimum of the ancestor deadline and the group's own deadline, plus
ancestor cancellation. A child cannot extend its parent budget. VM checks first
observe explicit cancellation, then deadline expiry (trap 6). The coordinator
stops admission, asks workers to stop and joins all started workers before
releasing reservations and returning; it never detaches a late writer.

Native entry and normal return pass through VM cancellation checks. Bytecode
instruction boundaries also check. Argument validation, heap admission, lock
reacquisition and cleanup can precede the next check. These are cooperative
boundaries, not a promise that an expired caller returns within a fixed physical
interval. Unexpected host exceptions retain their existing propagation/CLI
normalization; a deadline does not replace every already-raised exception.

A native's own timeout returns its documented typed error when it expires before
the VM context. An observed inherited cancellation or VM deadline retains its VM
meaning. Checks do not retroactively undo effects already performed. No shared
context means only the operation's separately specified limits apply. Trusted
hosts must restore thread-local deadlines when reusing a thread.

## Exhaustive native policy groups

The [inventory](../validation/deadline-boundaries/inventory.json) lists every
fixed native individually and all three compiler-provided conversion forms.
Its checker rejects missing, duplicate or obsolete names. Inventory coverage is
not proof of runtime latency, cleanup correctness or universal cancellation.

| Group | Delivery and remaining indivisible work | Effects and ambiguity |
| --- | --- | --- |
| Value operations | Lists, maps, strings, bytes, integer conversions, money, pure typed-time operations, JSON, structural assertions and generated conversions check at VM entry/return. Copying, sorting, parsing, formatting, hashing, validation and structural traversal run synchronously without interior cancellation polling. Existing value limits remain; they do not imply constant execution time. | No durable external publication. Computation and allocations may finish before the result is discarded. Heap tracing/cleanup rules below apply. |
| Clock, entropy and secrets | Wall/monotonic clock reads, random generation and environment-secret access are synchronous host operations. Cancellation is delivered at VM boundaries; clock/entropy host calls are not asynchronously interrupted. Secret reveal declassifies according to its existing authority/effect rules. | A clock/entropy read may occur before cancellation is reported. The runtime does not roll back host entropy consumption or previously revealed/logged data. |
| Sleep | Both sleep APIs use integer deadlines and requested waits of at most 50 ms, capped by the inherited remaining budget, checking between waits. Typed sleep also detects backward clock movement. | No durable write. OS scheduling can delay wakeup and the subsequent check. |
| Logging | `core.log.write` writes to the host's stderr stream and flushes synchronously. The VM checks before entry and after normal return; the runtime cannot interrupt a blocked host stream write/flush. | A partial or complete log line can be emitted before timeout. There is no rollback, delivery acknowledgment or automatic retry. A blocked sink can delay structured joining. |
| Files | Context checks surround descriptor-relative access and occur between requests of at most 65,536 bytes. Individual open, metadata, truncate, read, write and close calls remain synchronous. | Creation, truncation and partial writes can precede timeout. This is not atomic replacement; no rollback is implied. |
| Limiter | Shared state-lock acquisition consumes VM context and checks again after acquisition. Admitted refill/token updates complete under the lock. | Tokens may be consumed before a later timeout or outcome-telemetry failure. No automatic refund/retry. |
| Observation | Counters, notes, denials, outcomes, reports and evolution snapshots consume context at observation-lock admission. Admitted bounded sketch updates finish. | Terminal trap/HTTP telemetry may be omitted when context admission fails, preserving the original outcome. Sketches are not a lossless audit log. |
| Storage | Local/process lock waits and prepublication checkpoints consume context. The store retains its own five-second acquisition budget. Snapshot loading, serialization, encryption and individual filesystem/SQLite operations are synchronous. | Admission before replacement may reject without publication; entered replacement finishes fsync/cleanup. A timeout or `DbError` can coexist with a committed batch. Reconcile receipts. |
| Outbound HTTP/model | One 30-second transport budget, capped by VM deadline, covers URL/payload preparation and DNS/connect/TLS/write/read. URLs are limited to 8,192 UTF-8 bytes and request bodies/model JSON payloads to 1 MiB. Response reads poll with waits at most 50 ms. Host DNS is uninterruptible; connect/TLS/write cancellation may wait for the remaining socket timeout. | A remote server may commit even if the response is absent or truncated. No automatic retry. |
| Local provider | Unrestricted `core.model.local` imports and calls the optional companion synchronously. Its code has no VM-context parameter; cancellation is checked after a normal return. Restricted VMs reject this unmediated provider before import. | A trusted provider can perform host effects and block arbitrarily; VM mediation cannot promise rollback, termination or cleanup of provider-owned resources. Missing providers return `ModelError`. This repository's absent-provider tests do not qualify an installed companion. |
| Evolution | Minimum wave/VM budget covers spawned preparation and apply admission. Framed result reception polls; every started child is stopped/reaped. Source-lock/revalidation checkpoints precede journal admission. | Startup, serialization and reaping can exceed the budget. Entered journal recovery/commit finishes. Source may change before timeout; activation is next-process only. Owned proposal staging is removed after reaping/apply; cleanup can exceed the budget. Cleanup failure or abrupt parent death can leave files. Legacy caches are not swept. |
| HTTP serving | Serving context is checked by the accept loop and inherited by handlers. Queue/input/execution/output share each request's deadline, capped by a ten-second connection lifetime and 100-request quota. Normal shutdown drains admitted handlers; context cancellation aborts sockets and joins workers. | Response loss does not undo an application commit. Noncooperative handlers or host operations can delay final joining. HTTP serialization is additionally capped by the response byte/depth budget and polls context between emission chunks. Detailed admission and drain boundaries remain in the parallel amendment. |

## Heap and structured cleanup

The heap mutator lock, frame/pin removal, native-result handoffs, lock reacquisition
after a native/parallel operation, and mark/sweep run synchronously. Their purpose
is to preserve live roots and reclaim resources before control escapes. They are
not cancellable lock admission points. A slow mutator or collection can delay a
caller before its next VM check; no hard heap pause or native-memory budget is
established here. A telemetry failure after result production releases the
unreturned handoff. Parallel result ownership and worker reservations remain
held until all started workers finish.

Filesystem descriptors, sockets and evolution children owned by the runtime are
closed/reaped through their existing finally paths. This describes ownership and
completion obligations, not a claim that every OS close/join is interruptible.
A trusted host-injected native or optional companion can violate assumptions
outside those owned resources. Hostile execution requires the separate isolation
programme; a Python thread deadline is not an OS sandbox or forced termination.

## Commit reconciliation

The [transaction outcome rules](transaction-outcomes-amendment-2026-09-09.md)
define prepared/committed journal and receipt states. Cancellation observed at an
operation's final admission checkpoint prevents that operation's publication;
a race after admission may publish before the caller sees timeout. Missing
returns, missing HTTP responses, and omitted telemetry are not commit or rollback
oracles. Reconcile a stable request identifier against the durable receipt and
associated state before retrying. File writes and external logs/providers have
only their explicitly stated semantics, not database transaction semantics.

## Evidence and qualification boundary

The [retained review](../validation/deadline-boundaries/README.md) pins source,
policy groups, implementation locations and relevant regression evidence.
The specification covers the first criterion's propagation, delivery and ambiguity
scope. It makes no new latency percentile, fairness, aggregate memory, all-native
shutdown or production qualification claim. Those criteria require separate
implementation and workload evidence; listing a limit is not repairing it.

## Opt-in rollback authority

[Trusted snapshot generations](storage-rollback-amendment-2026-09-10.md) add authority-lock admission under the Store/VM budget. Snapshot hashing, authority/receipt writes, fsync and admitted recovery are synchronous. Once authority advancement begins, the staged publication is completed or retained for exact recovery before ordinary cancellation can escape; a timeout can coexist with an advanced generation. Missing authority or recovery evidence fails closed. Operator enrollment/status/restore commands are host maintenance, not new language natives.

[Live HTTP service-token rotation](http-credential-rotation-amendment-2026-09-10.md) rechecks the pinned credential file at each request gate, with VM checks before and after successful synchronous host reading. Missing or invalid files fail closed without handler dispatch; previously admitted requests retain their execution context.
