# Bounded storage read cache

This amendment refines the snapshot implementation described in
[the durable-storage amendment](storage-amendment-2026-09-05.md). It changes no
language syntax, stdlib signature, release number, bytecode format, persistence
format, or application contract.

## Change and expected benefit

Each `Store` now retains an LRU of up to 256 successful `get` results, including
absence, with at most 1 MiB of combined UTF-8 key and value payload. Oversized
results are returned normally without being cached. Python string and container
metadata cost additional memory; 1 MiB is not a process-memory ceiling. Many
Store instances each receive their own limit.

A cache hit avoids reading and deserializing the full SQLite snapshot. It still
opens the package/state path and database through the existing descriptor-relative
no-follow operations, checks regular-file type, single-link status and size,
and acquires the database lock. Cache misses and every write continue to load
the full snapshot. Large working sets, repeated writes and values above the
cache limit therefore retain the original full-snapshot costs. Measurements
must distinguish warm repeated reads from misses and writes; the cache is not
a replacement storage engine.

## Freshness and failure behavior

Under the existing `flock`, each read opens the current database and obtains its
`fstat` fingerprint: device, inode, size, nanosecond modification time and
nanosecond change time. A changed fingerprint invalidates all retained results.
Atomic replacement by a cooperating writer therefore invalidates other Store
instances and processes on their next operation. Missing database files also
invalidate the cache and remain uncached until a real snapshot exists. In-place
edits that change these timestamps invalidate the cache even if modification
time is subsequently restored.

On a miss, schema and requested-value validation are unchanged. The descriptor
is checked again after reading to reject an observed in-place change during
snapshot loading. A hit never bypasses fresh path/type/link/size validation.
It can reuse schema and value validation only while the fingerprint matches.
Fingerprints are a freshness mechanism for the documented local POSIX and
cooperating-writer model, not a content hash or authentication against a hostile
owner capable of manipulating files or filesystem metadata. Unsupported filesystems
with insufficient metadata/locking semantics are outside that model.

Every mutation clears the read cache before loading disk state. Failed
comparisons do not populate it. Failures within the locked operation clear it
as well, so a failed
pre-rename write cannot expose unpersisted SQLite changes, and a failure after
rename cannot return a stale cached predecessor. The next read reconciles the
actual current file. An error after rename remains an ambiguous write outcome;
callers must still read back state as described in the original amendment.
A timeout acquiring the per-Store mutex does not access or clear the cache;
the next successful operation still checks the current file under its lock.

A timed per-Store mutex serializes cache bookkeeping and temporary-root setup.
The mutex and filesystem-lock acquisition share one five-second deadline;
filesystem I/O and an already executing operation still have no forced deadline.
A contender may therefore fail with `DbError` while an earlier operation finishes.
The cache retains plain Python values only, with no open SQLite connection,
file descriptor, or cleanup thread. Connections used by misses and writes close
before the operation returns; cache memory is released with its Store.

After a process ID change, an inherited Store discards the cache and replaces
its Python mutex before operating. This avoids reuse of a mutex held by another
parent thread at fork. It is not a general guarantee for arbitrary fork during
active file I/O: a child can inherit an in-flight OS lock descriptor. Prefer
subprocess/exec, or fork while storage operations are quiescent. The cache does
not add retained OS resources to that existing fork limitation.

## Regression coverage

`python -m unittest gopyt.test_storage -v` exercises 24 tests, including the
original restart, competing-process CAS, persistence, confinement, size-limit
and native-error checks. New cases cover repeated reads and negative caching;
changed values from another process; removal and recreation; warm-cache
symlink, hardlink, FIFO, corruption, empty and oversized-file rejection;
same-inode edits with restored modification time; changed schema and value
types; UTF-8 payload and LRU entry bounds; pre-rename failure and post-rename
fsync failure; CAS through one Store shared by threads; bounded local-lock
waiting; and inherited cache/mutex reset after fork.

These are correctness regressions. Their duration is not a benchmark, and they
do not establish sustained throughput, absence of memory leaks, resistance to
hardware power loss, or faster snapshot writes. The follow-up benchmark report
records measured performance separately using the retained baseline and source
identities.
