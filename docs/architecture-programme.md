# Secure data-backend architecture programme

Status: active engineering programme; production readiness is not established.
Scope: the GoPyT language, runtime, Guard and tools. Model training is excluded.

The [2026-09-09 checklist review](architecture-checklist-review-2026-09-09.md)
maps all 193 original milestone/tracker checkboxes to the merged source and
retained or newly executed evidence. At that baseline, three criteria were supported;
all 24 architectural issues were open. Current issue states are tracked in the
[GitHub milestone](https://github.com/anthonykewl20/GoPyt/milestone/1). The review records three reproduced native or
configuration error-boundary defects and distinguishes existing v0 behavior
from requested extensions and deployment decisions. GitHub dependency links
describe proposed sequencing, not prerequisites enforced by the compiler.

The [native boundary amendment](native-boundary-amendment-2026-09-09.md)
repairs R1–R3 with compiled regression coverage. Broader issue requirements
remain open; the historical checklist review describes its pinned baseline.

## Acceptance gates

| Gap | Required outcome | Current increment |
| --- | --- | --- |
| Resource capabilities | Non-forgeable, least-privilege authority across database, filesystem and network; revocation and delegation semantics | Host-issued delegated DB/file/network/secret/listen authority implemented; authenticated HTTP tenant integration implemented; independent security/deployment qualification remains open |
| Resource lifetimes | Safe close, sharing and view lifetimes; bounded native-memory accounting; cancellation cleanup | Design gate open; mapped prototype remains outside the runtime |
| Transactional data | Atomic multi-record changes, consistent reads, recovery, schema evolution and a scalable backend | Bounded multi-key CAS and snapshot reads implemented; snapshot scalability and schema APIs open |
| Identity and keys | Tenant authorization, credential provisioning, rotation, migration and rollback detection | Tenant-bound sessions, live service-token rotation, trusted rollback detection, fenced writer-key transitions and explicit recoverable plaintext migration implemented; private-file key lifecycle procedures and recovery drills implemented; external deployment custody remains operator-owned |
| Stateful Guard | Operator-pinned transition, concurrency and fault acceptance through candidate execution | Transaction state-machine regression harness implemented; Guard integration open |
| Execution | A measured compiled/native path conforming to reference VM semantics | Reference VM retained; backend choice requires profiles and differential acceptance |
| Data interfaces | Exact numerical/time semantics, typed streaming/batches, bounded backpressure | Checked finite numeric, fixed-point money and typed time APIs implemented with oracle/data qualification; bounded transaction batches implemented; streaming remains open |
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

[Delegated resource authority](resource-authority-amendment-2026-09-09.md) adds
host-issued handles and inherited per-call scope. GoPyT code cannot construct or
widen these handles. Existing namespace policy and source egress remain required
where configured; this is not authenticated tenant identity or OS isolation.

[Verified request identity](request-identity-amendment-2026-09-09.md) binds broker-
issued credentials to subject/tenant grants at the real HTTP and native resource
boundaries. Deployment TLS/IdP/MFA responsibilities and independent audit remain
explicit; the milestone is not production-qualified.

[Toolchain upgrade qualification](toolchain-compatibility-amendment-2026-09-09.md)
binds locks and artifacts to exact runtime sources and exercises explicit
dependency review, rebuild and installed compiler/runtime rollback. Release
provenance and application data recovery remain separate qualification gates.

[Build-input qualification](release-inputs-amendment-2026-09-09.md) pins actions,
Python distributions and build/security wheels and verifies repeated wheel builds.
Signed provenance, complete bundled inventory and vulnerability response remain open.

Release provenance: [workflow, verification and withdrawal policy](release-provenance.md). Signed end-to-end qualification and complete component/adaptation inventory remain open under #23.

[Installed release component inventory](component-inventory.md) retains bundled metadata and its current coverage limits under #23.

[Numeric and time qualification](numeric-time-qualification.md) maps issue #17's
criteria to finite binary64 admission, exact fixed-point money, typed nanosecond
time, independent oracles, full retail replays and the IERS discontinuity replay.
The amendments define the bounded domains and explicit unsupported cases. These
results do not qualify the separate streaming, native-memory, production-security
or performance goals.

[Parallel admission and deadlines](parallel-admission-amendment-2026-09-10.md)
adds a shared VM worker bound, inherited deadlines and explicit overload behavior;
blocking-I/O and graceful-drain qualification under #13 remains open.

[Runtime resource qualification](runtime-resource-qualification.md) maps issue #13 to the shipped admission, cancellation and cleanup policies, with frozen five-scenario Linux stress evidence on both pinned Python versions and explicit ownership limits.

[Trusted snapshot generations and restoration](storage-rollback-amendment-2026-09-10.md) specify the opt-in operator authority, failure semantics and explicit restore workflow under issue #12. Full qualification is tracked separately.

[Live HTTP service-token rotation](http-credential-rotation-amendment-2026-09-10.md) specifies per-request reload, fail-closed admission and in-flight request semantics under issue #11.

[Storage writer-key fencing](storage-writer-fence-amendment-2026-09-10.md) specifies authority version 2, explicit generation-checked key transitions and rejection of stale active-key writers under issue #11.

- [Plaintext storage migration](plaintext-storage-migration-amendment-2026-09-10.md): explicit digest-checked initial encryption with a durable encrypted recovery copy.

- [Key and credential lifecycle](key-lifecycle.md): provisioning, custody, rotation/revocation, retention, retirement and lost-key recovery procedures with executable drills.
