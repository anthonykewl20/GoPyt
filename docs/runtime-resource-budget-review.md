# Runtime resource budget review

Issue #13, acceptance item A2: bound task queues, request admission, buffered data
and retries; define fairness and overload responses. This is a source review of
runtime commit `fb4fda0ef02389d70c2e7b5fa896ae5aea59c66c` (the full reviewed commit and runtime digest are retained
in [the outbound request inventory](../validation/outbound-request-budget/deadline-inventory.json)).
It records coverage and remaining qualification work; it does not change language
semantics or close A2. Historical validation directories qualify their named
snapshots, not every later runtime.

## Enforced bounds and ownership

| Resource | Bound and owner | Saturation / release policy | Evidence entry point |
|---|---|---|---|
| Structured parallel workers | At most 64 reservations per VM; host may lower to 1..64. Nested groups share reservations. | Atomic fail-fast trap 7; no capacity wait queue. Parent reservations remain held until joined cleanup. | `gopyt/scheduling.py`, `gopyt/test_parallel_admission.py` |
| Parallel pending arms | Language and bytecode allow at most 65,535 arms per construct. Scheduler consumes an index iterator; result and error arrays each have one slot per arm. | Workers take the next index under a mutex. No global FIFO or starvation guarantee. Rejection does not start another group. | `docs/implementer.md` section 7, `gopyt/vm.py` |
| HTTP connections | Per server: 64 workers and 1,024 queued connections. | FIFO pending connections; queue-full empty 503 if writable, then close. Ten-second nonrenewable connection deadline and 100-request quota; expired queued connections close without dispatch. | `gopyt/server.py`, `gopyt/test_connection_budget.py`, `gopyt/test_app_runtime.py` |
| Inbound HTTP body | 1,048,576 bytes per request. | Reject oversized framing/body before handler dispatch. | `gopyt/server.py`, `gopyt/test_app_runtime.py` |
| HTTP response encoding | 8,388,608 encoded bytes, traversal depth 128 per response. | Empty 500 before success headers on size/depth failure; context expiry closes through the deadline path. Handler effects may already exist. | `gopyt/jsonc.py`, `validation/http-response-budget/README.md` |
| Outbound request preparation | URL 8,192 UTF-8 bytes; HTTP body and complete remote-model JSON envelope 1,048,576 bytes. | Typed HttpError / ModelError before transport. Preparation shares the request budget; VM cancellation and timeout preserve their exceptions. | `validation/outbound-request-budget/README.md` |
| Outbound response body | 8,388,608 bytes per call, with one extra byte for oversize detection. | Typed failure; response transport closes. | `gopyt/natives.py`, `gopyt/netio.py` |
| File read buffer | Per-value allocation ceiling 2,147,483,647 bytes; 65,536-byte read chunks, with one extra byte for oversize detection. | Trap allocation on overflow; descriptor context closes. A successful bytes conversion can coexist with the bytearray. | `gopyt/natives.py` `_file_read`, `gopyt/ops.py` |
| Storage snapshot and batches | Store snapshot 64 MiB (encrypted envelope overhead separate); batch 256 keys and 8 MiB aggregate batch payload. | StorageError at boundary, mapped to typed DbError for native calls. | `gopyt/storage.py`, `gopyt/test_storage_transactions.py` |
| Storage read cache | Per Store: 256 entries and 1 MiB combined UTF-8 key/value payload. | LRU eviction; oversized results bypass cache. Python container overhead is separate. | `docs/storage-cache-amendment-2026-09-05.md`, `gopyt/test_storage.py` |
| Rate limiter | Default VM instance: 4,096 hashed-key buckets. | Expire fully refilled entries; fail closed if full or policy changes for a live key. | `gopyt/limiter.py`, `gopyt/test_app_runtime.py` |
| Observation | Per VM: Count-Min 5 × 272 cells, Bloom 1,024 bytes, scalar moments/CUSUM, and at most configured K compact reservoir records. | Inline updates, Algorithm R replacement, no deferred event queue. Context-expired terminal telemetry can be omitted. | `gopyt/observe.py`, `gopyt/test_validation.py`, `gopyt/test_observe_deadlines.py` |
| Evolution | One admitted proposal per VM, configured candidate limit; parent result frame at most 16 KiB. | Reject overlapping proposals; parent validates frame and joins/reaps child before returning. | `docs/deadline-boundaries.md`, `validation/evolve-deadline/` |
| Identity authority | Per authority: 1,024 policies and 4,096 sessions. | Reject capacity overflow; session expiry/revocation is enforced. | `gopyt/identity.py`, `gopyt/test_identity.py` |

## Configured capacity is a real parameter

Reservoir K defaults to 32. When an artifact declares evolution, VM construction
uses the largest declared reservoir K. The compiler and bytecode validator both
accept 1..2,147,483,647; the list grows lazily to K. Thus 32 is not a universal
runtime cap, and a syntactically valid setting need not fit the deployment's RAM.
Each retained event compacts task and tag to at most 256 UTF-8 bytes (longer text
becomes a SHA-256 label); JSON escaping and Python object overhead remain.
Snapshot/dump creates additional materialized copies. `memory_cells()` is a
logical capacity count, not a measured byte or RSS ceiling.

The declared evolution candidate and timeout limits have the same positive i32
range. They bound a wave's declared work, without making every allowed value
operationally affordable. Changing these documented ranges needs an explicit
language amendment, not an undocumented clamp in the VM.

## Retries and fairness

The shipped outbound HTTP/model transport performs one request attempt and
disables redirect following. It does not automatically replay an effect after
failure. Lock polling and partial transport writes advance one operation under
its existing budget; they are not application-operation retries. Application
retry loops and their reconciliation/idempotency contracts remain application
policy. A timeout after publication is not proof that a durable effect failed.

HTTP FIFO orders pending connections, not requests or tenants. Worker scheduling,
OS backlog, concurrent host invocations, and noninterruptible native work prevent
a strict service or starvation-freedom guarantee. Connections admitted close in
time may expire together while waiting. The documented quota and cooperative
lifetime limit occupancy; they do not promise a latency percentile under load.

## Acceptance boundary and remaining review

These are resource-specific bounds. They do not establish an aggregate heap/RSS,
process/thread, file-descriptor or disk quota across arbitrarily many VMs, Stores,
authorities, host callers or application values. Source and bytecode size limits
also do not establish an aggregate application object-graph limit. Serialization
scratch space, sorting keys, decoding and success-path copies must be counted
separately from final payload limits.

Before checking A2, complete the boundary inventory for HTTP request/header and
trailer parsing, generated conversions, local model/provider buffering, and
persistent evolution candidate/cache accumulation. Classify each as fixed,
configured, caller-owned, or still unbounded; retain supporting source and focused
boundary evidence. In particular, a body cap alone is not proof of a total HTTP
connection-memory cap. Review all native groups in the deadline inventory rather
than inferring completeness from networking tests.

A3 still requires a cleanup-ownership review covering server admission/drain,
worker and child joins, descriptor release, and durable commit acknowledgment.
A4 requires retained saturation/cancellation/shutdown workload trials with latency
and leak observations, failed trials and an independent correctness oracle.
Existing unit regressions establish individual boundaries; they do not substitute
for that sustained workload qualification. Aggregate runtime memory and broader
production qualification also intersect issues #5, #8 and #18.
