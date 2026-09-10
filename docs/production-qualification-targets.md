# Frozen production workload, threat model and qualification targets

This document freezes the decisions issue #3 requires: one deployment envelope,
one attacker model, one set of acceptance thresholds, one control allocation and
one frozen dataset/oracle identity set. Freezing a target is not evidence that
the target is met. Every threshold here is a requirement for later qualification
under #25; several are deliberately beyond what the current
implementation can reach, and those gaps are named in each section.

The machine-checkable copy of every value below is
[`validation/workload-freeze/targets.json`](../validation/workload-freeze/targets.json),
verified by `tools/check_workload_freeze.py` and by
`gopyt/test_workload_freeze.py` in the language suite. The checker fails if the
frozen values, dataset identities or oracle identities change without an
explicit revision bump, so tuning cannot silently move a threshold it failed.

## 1. Deployment envelope

The selected initial target is a **single-node Linux container backend service**.

| Property | Frozen value |
|---|---|
| Primary use cases | Transactional order/inventory-shaped OLTP over HTTP: authenticated JSON request/response, small conditional read-modify-write batches, replayed ingestion of line-item records, and operator maintenance (snapshot, rotate, restore). |
| Supported production platform | Linux x86-64, glibc, container runtime, CPython 3.11.16 and 3.14.7. |
| Development-only platform | macOS arm64 (macos-15). Qualified by CI for language behavior only; not a supported production deployment. |
| Hardware | 4 vCPU, 8 GiB RAM, 100 GiB SSD-backed volume. |
| Enforced process memory | 2 GiB, enforced externally by the container (cgroup v2 `memory.max`). |
| Retained application state | 10 GiB, so retained state exceeds the enforced process memory limit by 5x. |
| Concurrency | 50 concurrent client connections. |
| Offered load | 200 requests/second sustained. |
| Transport | Plain HTTP on a numerical loopback address behind a trusted TLS-terminating gateway on the same node; the gateway supplies forwarded identity. |

Unsupported at this envelope, by decision rather than omission: multi-node
clusters, horizontal replication, cross-node consensus, Windows, 32-bit targets,
non-glibc Linux, GPU or accelerator execution, and hostile in-process code.

**Current gap.** The shipped storage backend bounds one snapshot at 64 MiB and
rewrites it whole. It cannot hold 10 GiB of retained state, and the greater-than-RAM
requirement above is exactly the requirement #8 must satisfy. The 200 req/s and
50-connection figures are within the shipped per-server bounds of 64 workers and a
1,024-connection queue, but no measurement against them exists yet; that is #25.

## 2. Unified attacker model and trust boundaries

One model spans every surface. The attacker capabilities admitted here are the
ones the project claims to resist; anything not listed is out of scope and must
not be described as defended.

| Surface | Admitted attacker capability | Boundary that must hold | Status |
|---|---|---|---|
| Compiler | Supplies untrusted GoPyT source and package manifests to a trusted build host. | Rejects malformed, contract-weakening and lock-mismatched sources with diagnostics; never executes application source at compile time. | Implemented and regression-tested. |
| Bytecode / artifact | Supplies a forged, truncated or toolchain-mismatched `.gobyte`. | Format and exact toolchain fingerprint are verified before execution; mismatch is a load error. | Implemented (#22). |
| VM | Supplies application code that attempts unbounded allocation, unbounded recursion, effect escalation or non-terminating work. | Closed native catalogue, effect checking, per-value allocation ceiling, configured resource budgets, deadlines and cancellation. | Partially implemented; native/off-heap accounting is #5, which remains open. |
| Natives | Supplies hostile paths, oversized payloads and malformed encodings through the closed catalogue. | Path confinement (no symlink traversal, hard links, `.git`, `.gopyt-state`, source/build/manifest writes), explicit size ceilings, typed failures. | Implemented; allocation accounting inside SQLite, TLS and cryptographic natives is not (#5). |
| Host process | Runs code that tries to escape the interpreter to the operating system. | **Not defended.** The VM is not an OS sandbox. Isolation must be supplied by the container/service account, and #6 tracks a defined isolation profile. | Not implemented. |
| Editor / LSP | Supplies large or pathological local package snapshots to the checker. | Bounded snapshots, CPU/wall limits and a Linux address-space limit. | Partially implemented; not a multi-tenant compilation service. |
| Guard | Supplies a candidate that tampers with acceptance definitions, helpers, fixtures or oracle identity to obtain a pass. | Bundle hash and engine pin, transitive contract-helper closure within one package, disposable evaluation tree. | Partially implemented; cross-package closure is #15 and stateful/effectful acceptance is #14. |
| Credentials | Reads process memory, the filesystem, or replays an old snapshot or an old token. | Private 0600 key/token files outside the package, fail-closed strict mode, AES-256-GCM-SIV snapshot authentication, live token rotation, writer-key fencing, opt-in rollback authority. | Implemented (#11, #12); memory inspection by a same-account or root process is explicitly not defended. |
| Deployment | Reaches the service without passing the gateway, or floods it. | Loopback-only strict listeners, a configured trusted-gateway peer that fails closed for every other address, forwarded identity believed only from that peer, this server's own header bounds, identity-aware admission, connection quota, request deadline, queue-full 503. | Implemented and qualified through real network paths in #24; the gateway itself and the network path to the listener remain the deployment's. |
| Supply chain | Substitutes a release artifact or a build input. | Hash-pinned build inputs, reproducible wheels, release provenance and withdrawal policy. | Partially implemented; signed end-to-end qualification is #23. |

Explicitly outside the model at this envelope: hostile code executing in the host
process, a compromised host or root account, physical access, side-channel and
timing analysis, denial of service beyond the configured bounds, and the security
of the external TLS gateway itself.

## 3. Correctness invariants and budgets

### Correctness invariants (must never be violated)

1. Every accepted write is either durably published or reported failed; no
   acknowledged write is lost after a restart.
2. Replayed or duplicate ingestion never double-applies a line item; per-SKU
   totals equal the independent oracle exactly.
3. A read never returns data from another operator-selected namespace, tenant or
   session than the request is authorized for.
4. An authenticated snapshot never decrypts under a wrong key or wrong store
   identity, and an older valid snapshot is detected as replay when rollback
   authority is configured.
5. Monetary and temporal values are exact: no binary floating point, no silent
   rounding, no non-finite values crossing a boundary.
6. A trap, cancellation or deadline never leaves a descriptor, mapping or
   resource reservation charged after the owning scope ends.

### Budgets and objectives

| Target | Frozen value | Measured by |
|---|---|---|
| Request latency, p50 | 25 ms | #25 |
| Request latency, p99 | 250 ms | #25 |
| Request latency, p99.9 | 1,000 ms | #25 |
| Sustained throughput | 200 requests/second at 50 concurrent connections | #25 |
| Resident memory | 2 GiB (2147483648 bytes) hard cap, enforced by the container; steady-state target 1.5 GiB (1610612736 bytes) | #25 |
| Availability | 99.5% monthly, measured at the gateway | #25 |
| Recovery point objective (RPO) | 0 committed transactions lost | #25 |
| Recovery time objective (RTO) | 15 minutes (900 seconds) from node loss to serving | #25 |
| Cold start to first served request | 30 seconds | #25 |
| Planned restart drain | 10 seconds, matching the non-renewable connection deadline | #13, #24 |

No measurement against these values exists in this repository yet. They are
requirements, not results. No release may be described as meeting them before
#25 measures them and #26 completes an independent security review.

## 4. Control allocation

| Control | Owner |
|---|---|
| Type, effect and contract checking; exact numeric and temporal semantics; allocation ceilings; determinism of the formatter and bytecode | Language |
| Resource budgets and admission, deadlines, cancellation, structured parallelism, descriptor and buffer lifetimes, tracing heap, closed native catalogue | Runtime |
| Business invariants, idempotency keys, retry and reconciliation policy, namespace layout, request authorization decisions, schema evolution | Application |
| TLS termination, forwarded identity, network exposure, OS isolation and service account, memory/CPU limits, secret custody and rotation schedule, backups and restore drills, monitoring, alerting, incident response, capacity planning | Deployment |

Scenarios explicitly not supported at this envelope: running untrusted
application code in the same process, multi-tenant isolation inside one VM,
horizontal scale-out, live schema migration without operator sequencing, and any
use of the LSP or Guard as a hosted service for untrusted input.

## 5. Frozen datasets, provenance, oracles and thresholds

| Item | Frozen identity |
|---|---|
| Primary dataset | UCI Online Retail (Chen, D., 2015; DOI 10.24432/C5BW33), CC BY 4.0, source workbook SHA-256 `43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d` |
| Volume | 541,909 rows, 25,900 invoices, 4,070 SKUs, quantity sum 5,176,450, including 10,624 negative-quantity and 9,288 cancellation rows |
| Privacy handling | Projection retains row number, invoice, stock code and integer quantity only. Customer identifiers, descriptions, dates and prices are dropped before use. |
| Prepared rows | `rows.jsonl` SHA-256 `08f76b68f072e3caf24ae98cfedc256fd0522d95290f8d7b41d63544b72df821` |
| Expected results | `expected.json` SHA-256 `41d87a5d8c2a19713d325a6d8b94bbfff966676b45cf5598968eec41e5da037a` |
| Independent oracle | `oracle.sqlite3` SHA-256 `0c199077a98a1c6145b563424802a38437822177cbfd1e98bc1d8f16c11101ce`, computed by SQLite `GROUP BY`/`SUM` outside the VM |
| Retained manifest | `validation/architecture/dataset-manifest-v2.json` |

Representativeness is asserted only for the ingestion and per-SKU aggregation
shape of the frozen use cases. This dataset is 541,909 source rows that reduce to
roughly 0.52 MiB of persisted state; **it does not by itself exercise the 10 GiB
retained-state target.** #25 must add a separately frozen synthetic or derived
corpus that reaches 10 GiB with its own independent oracle, and must not relabel
source-row volume as database size.

Acceptance thresholds are frozen here before any tuning against them. A run that
misses a threshold is reported as a miss; the threshold is changed only by a
revision bump in `targets.json` with the reason recorded, never by editing a
result.

## 6. Status of the gaps this freeze created

`validation/workload-freeze/targets.json` carries the machine-readable list, and
its revision history is how an entry leaves. Revision 2 removed #24 after the
gateway, header and outbound address boundaries were implemented and qualified
through real network paths, and restated #6 to describe what the merged Linux
isolation profile does and does not cover. No target, threshold, dataset
identity or envelope value has changed since revision 1.

Still unmet: the backend cannot hold the frozen retained state (#8), native and
cryptographic internal allocations are unaccounted (#5), the isolation profile is
qualified on one Linux kernel with no system-call filter and no tenant-separation
evidence (#6), no measurement exists against any latency, throughput,
availability or recovery objective (#25), and no independent security review has
been performed (#26).

## 7. Change control

`validation/workload-freeze/targets.json` carries `revision`, `frozen_at` and a
self-digest over its own frozen content. `tools/check_workload_freeze.py`
recomputes the digest, re-verifies every referenced dataset hash against the
retained manifest, and checks that this document and the JSON agree on every
frozen scalar. Amending a target means bumping `revision`, recording
`revision_reason`, and updating both files in the same change.
