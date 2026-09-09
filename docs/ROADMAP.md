# Language-first engineering programme

This repository is the GoPyT language workspace. The separate model-training
workspace is not part of this release.

The active [architecture programme](architecture-programme.md) tracks the expanded
production goals and their unclosed acceptance gates. Atomic batches, service
database authority and authenticated key rotation are the first implementation
increment; see [batch storage](batch-storage-amendment-2026-09-09.md) and
[key rotation](storage-key-rotation.md).

Cybersecurity takes precedence across compiler, tooling, persistence and HTTP.
Independent review, deployment authorization, encrypted migrations and rollback
protection remain explicit follow-up work.

The current delivery increment addresses each priority with implementation,
measured acceptance and explicit remaining limits:

1. Stateful backend: GoPyT inventory reservations with runtime invariants,
   idempotent commands, CAS persistence, cancellation, confirmation, expiry,
   concurrent requests and restart/failure testing against an independent model.
2. Compiler/VM: generated-program differential checks and regression preservation.
3. Developer experience: an initial bounded LSP with real diagnostics, formatting,
   document symbols and completion; reliable protocol and overlay tests.
4. Practical integrations: typed HTTP/JSON, durable storage and clock behavior
   exercised through the complete inventory service rather than disconnected APIs.
5. Runtime scalability: before/after no-op persistence measurements and controlled
   concurrent load with latency, correctness and resource observations.
6. Release discipline: independent language packaging, clean-install verification,
   Linux/macOS CI, source inventory and publication of a reviewable GitHub baseline.
7. Data-driven backends: measure mmap against streaming/buffered access on immutable
   fixed-width columns. Verify exact results, bounded access, format validation,
   resource use and failure behavior before promoting a mapped native API.

Memory mapping is not automatically transaction durability, synchronization,
zero-allocation parsing or GPU-accessible memory. Initial data work targets
read-only column scans and indexed reads. Shared-memory IPC, mutable mapped
transactions, compressed column formats and genuinely beyond-RAM stress require
separate protocols and measurements. Results will distinguish implementation
from experiments and completed validation from configured CI.
