# Native resource accounting audit

Status: implementation gap inventory for issue #5, not qualification evidence.
The buffer ledger currently accounts for buffer payloads, transient buffer copies,
and open buffer/view handles. It does not impose an aggregate native-memory or
descriptor limit on the following existing paths.

| Path | Acquisitions and overlapping storage | Required integration |
| --- | --- | --- |
| `files.py`, filesystem natives in `natives.py` | Directory traversal descriptors, target/staging files, bounded immutable read chunks and final bytes | VM core.file read/write now reserve each descriptor before open, including parent/child overlap; read chunk/final-copy reservations are implemented. Non-VM callers and atomic_write still require admission and publication-failure ownership integration |
| `netio.py`, HTTP client natives in `natives.py` | Connection attempts, live sockets, TLS state, response bodies and decoding | Own a reservation across connection and response lifetime; account connection replacement and body copies; define a bounded TLS allocation policy |
| `server.py` | Listener, accepted connections, buffered request bodies and response serialization | Reserve before accepting ownership and before body allocation; retain charges through worker completion and server shutdown |
| `storage.py` | Root/directory/lock/database/staging descriptors, encrypted and plaintext database images, SQLite in-memory database, serialized output | Share the VM ledger through the store; charge simultaneous images and database allocation; keep publication and lock lifetimes covered |
| `rollback.py`, `migration.py`, `store_admin.py` | Recovery files, temporary files, backups, SQLite and encryption copies | Define explicit administrative budgets where no VM owns the operation; preserve cleanup and recovery semantics on budget rejection |
| `security_config.py`, `guard.py`, `transaction.py` | Configuration and policy descriptors, traversal overlap, journals and bounded read buffers | Associate ownership with execution or administrative context; retain existing size bounds in addition to ledger admission |
| `jsonc.py`, `observe.py` | Encoding bytearrays/StringIO and observation bitsets | Distinguish managed output from native scratch and charge overlap before allocation |

This is a source review inventory, not a claim that every allocation is listed.
Compiler, build, LSP and project-management allocations also require an explicit
scope decision. Third-party SQLite, TLS and encryption allocations cannot be called
accounted merely because their input and output byte lengths are charged. Their
internal allocator behavior needs a supported bound, allocator integration, or an
isolated execution policy with separately qualified limits.

## Mapping admission decision

An immutable mapping constructor must reserve mapping length and descriptor capacity
before acquisition. A sealed Linux memfd requires the original descriptor while
being populated and sealed. CPython's default Unix `mmap` constructor duplicates
the supplied descriptor: the pinned reference at commit
`823f0323ee6ec1402088b73bce1a38473cac36dc`, `Modules/mmapmodule.c`,
`new_mmap_object`, calls `_Py_dup(fd)` when `trackfd` is true, before `mmap`.
Therefore charging only one descriptor during that constructor is insufficient.
Use the compatible default constructor with two-descriptor peak admission unless
a separately verified platform/version profile changes that requirement.

A process-level probe on the current Linux x86_64 host confirms descriptor deltas
1 → 2 → 1 → 0 for create, map, original-close and map-close on Python 3.11.16
and 3.14.7. Both reject writes to the sealed backing and preserve the mapped data
after original-close. The [probe and results](../validation/resource-lifetimes/mapping-admission/)
record this host evidence; they do not qualify the GoPyT API or other platforms.
Both pinned builds omit Python sealing constants. The successful probe uses the
Linux UAPI numbers verified against installed headers and guards the platform;
the initial AttributeError trial is retained alongside the successful results.
After successful construction, close the original descriptor and release only its
reservation. Keep the mapping's descriptor and mapped-byte reservations until
physical unmap/close succeeds. Constructor failure must unwind each acquired stage;
uncertain cleanup must retain an owner and its charge for retry. A mapping of an
externally writable file is not an immutable backing object.

Before implementation, define empty-input behavior, sealing capability failure,
platform support, cancellation between acquisition stages, and typed native errors.
Qualify actual descriptor deltas and mapping lifetime on each supported profile,
including nested views, GC, and concurrent close/access. No mapping API is shipped
by this audit document.
