# Durable storage and conditional updates

This amendment supersedes the implementation's per-VM dictionary storage.
It does not change the toolchain release number or bytecode format. The shipped
stdlib gains one native, `store.db.compare_exchange`, with the declaration in
[stdlib.md](stdlib.md). Its host `ffi` effect does not propagate to applications.

## Observable semantics

An explicit package root selects `.gopyt-state/store.sqlite3`. All tasks and VMs
using the same root share this store, including after process restart. Different
package roots are isolated. CLI run/test commands supply the package root;
therefore test writes also persist and tests must choose/reset their fixtures
explicitly. Embedders constructing `VM(artifact)` without a root retain isolated,
temporary per-VM storage; `VM(artifact, root)` opts into that root's persistent
storage. The temporary storage directory is cleaned when its Store is released.

`get` returns the stored string, `NotFound` when absent, or `DbError` for storage
failure. `put` atomically inserts or replaces a value and returns `unit` or
`DbError`. Neither operation supplies an application transaction spanning calls.

`compare_exchange(key, expected, value)` is one atomic operation:

* `expected = none`: insert only if the key is absent.
* `expected = some(text)`: replace only if the key exists with exactly that text.
* Return `true` after successful persistence, `false` for a failed comparison,
  or `DbError` for a storage failure. Empty strings remain distinct from absence.

All three require a nonempty key. Comparisons are exact strings; there is no
version counter, normalization, multi-key transaction, deletion API or ABA
protection. Applications needing revision semantics must encode a monotonic
revision in each stored value and compare the entire previous value.

Missing database files initialize an empty store. Stopping all users and
removing `.gopyt-state` explicitly resets application state. The runtime does not
authenticate externally supplied, structurally valid SQLite files. Corrupt data,
unexpected schemas, unsafe file types and operation failures return `DbError`;
there is no silent in-memory fallback. Bytecode/source digests exclude runtime
state. Back up the database only while writers are stopped or by copying a
single opened snapshot file; copying the live directory is not a coordinated
application backup protocol.

## Implementation and bounds

SQLite operates on in-memory snapshots; its filename APIs never open package
paths or journal sidecars. Package and state ancestors are opened with
descriptor-relative no-follow traversal. The database and lock must be regular
files with one hard link. State directories are created mode 0700, files 0600.
Descriptor-relative atomic replacement cannot follow a destination symlink.

Each operation holds an advisory `flock` across load, comparison and persistence.
Lock acquisition waits at most five seconds before returning `DbError`; disk
I/O itself is not asynchronously interrupted. VM-owned stores additionally obey
the [storage admission and publication checkpoints](parallel-admission-amendment-2026-09-10.md#storage-admission-and-publication).
Successful writes fsync the replacement,
atomically rename it over the database, and fsync the directory before returning.
A process killed before rename leaves the old state. The next operation removes
orphan temporary snapshots. A process killed after rename can leave the new
state even without a returned success; callers must reconcile ambiguous failures
by reading state. An fsync error after rename has the same ambiguity.

These guarantees assume a local POSIX filesystem that honors locking, atomic
rename and fsync. They do not promise distributed/network-filesystem correctness
or protection from an uncooperative process with the same filesystem authority
replacing lock files/directories while an operation is active. State path
symlinks and existing hard links are rejected, but this is not authentication
against the package owner. No power-loss fault injection has been performed.

The serialized database is capped at 64 MiB; each key, value or expected value is
capped at 32 MiB of UTF-8. Oversized inputs and growth failures return `DbError`
without replacing the prior snapshot. Reads and writes allocate full snapshots;
peak process memory can substantially exceed the file size. The SQLite page
limit bounds database growth, not total host process memory. Every operation is
serialized, and write work grows with the entire database size. This is suitable
for measuring the first small application, not an asserted high-throughput or
large-data storage solution. A future storage backend must preserve these
observable semantics and pass the same process-boundary regression tests.

## Reproductions and validation

Before the change, writing `ticket=v1` and reading from a second VM returned
`NotFound`. Two threads synchronized after reading `v1` both accepted conflicting
updates (`v2a` and `v2b`) through separate get/put calls. Those observations justify
durability and conditional mutation as application blockers.

`python -m unittest gopyt.test_storage -v` checks restart after abrupt process
exit, 8-process stale-write competition (exactly one winner), 4-process CAS
increments (120 retained increments), thread competition, package/implicit-VM
isolation, empty and Unicode strings, directory/file symlinks, hard links, FIFO,
corruption, lock timeout, disk-failure injection, pre-rename process crash,
orphan cleanup, size limits, compiler effect propagation and native `DbError`.
Application HTTP acceptance tests and performance measurements provide the
separate end-to-end evidence; unit test duration is not a storage benchmark.
