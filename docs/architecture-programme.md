# Secure data-backend architecture programme

Status: active engineering programme; production readiness is not established.
Scope: the GoPyT language, runtime, Guard and tools. Model training is excluded.

The [2026-09-09 checklist review](architecture-checklist-review-2026-09-09.md)
maps all 193 original milestone/tracker checkboxes to the merged source and
retained or newly executed evidence. Three individual criteria are supported;
all 24 architectural issues remain open. It records three reproduced native or
configuration error-boundary defects and distinguishes existing v0 behavior
from requested extensions and deployment decisions. GitHub dependency links
describe proposed sequencing, not prerequisites enforced by the compiler.

## Acceptance gates

| Gap | Required outcome | Current increment |
| --- | --- | --- |
| Resource capabilities | Non-forgeable, least-privilege authority across database, filesystem and network; revocation and delegation semantics | Host-owned database namespace policy implemented; remaining resources and language-level delegation open |
| Resource lifetimes | Safe close, sharing and view lifetimes; bounded native-memory accounting; cancellation cleanup | Design gate open; mapped prototype remains outside the runtime |
| Transactional data | Atomic multi-record changes, consistent reads, recovery, schema evolution and a scalable backend | Bounded multi-key CAS and snapshot reads implemented; snapshot scalability and schema APIs open |
| Identity and keys | Tenant authorization, credential provisioning, rotation, migration and rollback detection | Bounded keyring and authenticated rekey implemented; tenant identity, plaintext migration and rollback detection open |
| Stateful Guard | Operator-pinned transition, concurrency and fault acceptance through candidate execution | Transaction state-machine regression harness implemented; Guard integration open |
| Execution | A measured compiled/native path conforming to reference VM semantics | Reference VM retained; backend choice requires profiles and differential acceptance |
| Data interfaces | Exact numerical/time semantics, typed streaming/batches, bounded backpressure | Bounded typed transaction batches implemented; streaming and numeric extensions open |
| Tooling | Type-directed workspace operations, dependency analysis, debug/profile facilities | Initial LSP retained; further implementation open |

## Rules for evidence

- Freeze source and dataset identities for each trial. Keep failed trials.
- Identify source rows separately from derived operations and synthetic repetition.
- Compare exact results with an independent oracle, including negative values,
  duplicates, absent values and cancellations present in source data.
- Exercise compiled GoPyT entry points, multiple processes, encryption, restart,
  stale conditions and failures around publication. A host-only test is labelled.
- Report latency distributions, errors, conflicts, retry budgets, memory and
  workload bounds. A benchmark outside the RAM limit must actually constrain RAM.
- Process termination is not a power-loss test. A public historical dataset is
  not a customer's deployed production workload. Neither establishes universal
  security, linearizability of arbitrary applications or production certification.

## Transaction milestone

An invoice or command can include its idempotency marker and all affected keys
in one conditional batch. Every condition uses one locked snapshot. A conflict
changes nothing. Successful changes publish once. Reads spanning keys use the
batch snapshot API; multiple independent `get` calls are not a snapshot.

The next backend must preserve these semantics and the existing descriptor-based
confinement and authenticated-storage guarantees. Replacing the snapshot backend
with a direct SQLite path without a sidecar/confinement design is not equivalent.

No new runtime dependency is selected by reference research. The existing SQLite
host module and separately selected cryptography extra remain the implementation
facilities for this increment.
