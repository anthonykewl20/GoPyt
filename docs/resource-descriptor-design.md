# Descriptor ownership integration

Status: next implementation contract for issue #5. The Buffer/mapping increment
does not yet charge descriptors acquired by existing file, network or storage paths.

## VM ownership and admission

Add a VM-owned descriptor registry sharing the VM's ResourceBudget. Each descriptor
has an internal owner allocated and registered before the OS acquisition. Reserve
one descriptor before each open, including simultaneous parent/child traversal
descriptors. Registry admission must happen before open, so registry allocation
failure cannot strand an acquired descriptor. If open fails, remove the empty owner
and release the unused reservation. Expose no raw descriptor in the language API.

The registry differs from language heap roots: it holds internal acquisition and
cleanup state throughout native calls, even before a language result exists. GC
must not close an in-progress acquisition merely because there is no managed root.
VM teardown may drain the registry only after call admission has stopped and active
calls have finished, matching the existing VM.close contract. Parallel calls share
the registry and budget; registration/removal is synchronized, while OS close runs
outside the registry and heap locks.

## Close and uncertain outcomes

An owner detaches its descriptor number under its state lock before issuing close.
Successful close releases the reservation and removes registry ownership exactly
once. A raised close leaves a quarantined owner and conservative charge, with the
error category recorded. Never retry a detached raw number: the OS may already have
released and reused it. Repeated owner close reports unresolved cleanup without
another raw close call. Idle VM.close must return false if quarantined ownership
remains; context teardown reports ResourceCleanupError. This is an explicit failed
cleanup result, not evidence that the descriptor was physically released.

Keep OS acquisition, body, and cleanup errors distinguishable internally. Cleanup
failure must not erase a cancellation or original operation failure; retain both
the original error and cleanup state without logging file contents or credentials.
A host must retain/report unresolved teardown. OS worker termination and isolation
are separately qualified controls, not a substitute for correct owner bookkeeping.

## First consumer: core.file

Thread the registry through regular_file and parent_directory for VM calls, while
keeping non-VM callers explicitly outside this first integration. Transfer each
parent/child owner only after child acquisition succeeds. If parent close fails,
the child still has a registered owner and must be closed during unwind. The target
file owner stays live until its stream closes; fdopen(closefd=False) does not own
the descriptor and must not release its charge. Reserve before creation/truncation,
and preserve the existing cancellation checks before mutation.

Descriptor capacity exhaustion uses allocation trap 14, consistent with shared
file-read scratch exhaustion; do not add a new result variant or effect to core.file.
Reads and writes share the same descriptor capacity with mapped Buffers. A failed
admission must neither create a missing output file nor truncate an existing file.
Administrative atomic_write, storage, network, accepted sockets and SQLite/TLS
internal resources remain separate required consumers of this ownership discipline.

## Qualification

Test actual compiled reads/writes with capacities zero, one and two; verify no
mutation before rejected admission, parent/child/target overlap, short I/O,
cancellation at each acquisition boundary, and return to the exact budget baseline.
Use real descriptors to inject a close that physically succeeds then raises, reopen
that number, and verify cleanup cannot close its replacement. Also inject open and
registration failures and parent-close failure after child acquisition. Run parallel
calls sharing a budget and verify aggregate admission and VM teardown reporting.
Retain evidence on Linux and macOS; do not infer one platform's ambiguous-close
behavior from the other or call conservative quarantine successful cleanup.

## Working implementation status

The descriptor registry is now attached to each VM and shares its construction-time
ResourceBudget. VM.close drains it after idle admission stops; unresolved descriptor
cleanup makes teardown return false. core.file read/write pass the registry through
parent-directory traversal and regular-file acquisition. Parent ownership transfers
to the child before closing the old parent, so failure still unwinds the child.
Body exceptions retain precedence while cleanup remains separately visible in the
registry. Non-VM file helpers and other native resource paths remain outside this
increment. Focused compiled tests cover zero/one/two descriptor budgets, rejection
without output creation/truncation, and parent-close failure with child cleanup.
Full and cross-platform qualification of this increment remain pending.

## Storage integration sequence

Storage key loading must use the same VM registry as the database, rollback anchor
and staging files. Integrate that path first without implying that remaining opens
are accounted. Descriptor capacity rejection during a store operation becomes its
existing DbError, like the existing database size limit; it must occur before the
rejected open and must not expose key material. Private-file validation remains
unchanged. Direct administrative callers without a VM retain their existing API
and are explicitly outside this initial registry integration.

Key-file and snapshot-file opens now use the VM registry. Snapshot acquisition
recognizes FileNotFoundError only while opening; exceptions raised by its caller
propagate unchanged and still close the descriptor. Directory, database-lock,
rollback-anchor and receipt descriptors remained to be integrated at this step.
This partial implementation does not establish aggregate storage resource accounting.

Storage directory traversal and database-lock acquisition now share the registry,
with a scope installed before opening the storage directory so parent-close failure
still unwinds that directory. Lock-creation retries preserve their prior deadline
and exclusivity checks. Directory enumeration reserves one temporary descriptor: the
CPython reference `Modules/posixmodule.c` at
`823f0323ee6ec1402088b73bce1a38473cac36dc`, `_posix_listdir`, duplicates fd inputs
for fdopendir and closes that temporary directory stream on exit. This internal
descriptor stays owned by CPython, rather than exposing a raw owner to the VM.

A non-anchored read requires capacity for three overlapping descriptors (directory,
lock, and enumeration or snapshot). Tests reject capacities zero through two without
changing the existing database and verify exact budget return at capacity three.
Rollback anchors and receipts remain unintegrated; this is not
a complete native descriptor or memory accounting claim.

### Publication staging

The pending snapshot now reserves its descriptor before exclusive creation. Its
stream does not own the descriptor; registry cleanup follows stream flush and
file fsync, before publication admission or rename. A failed creation never
unlinks an existing staging name. Write failures remove unadmitted staging,
while admitted recovery staging retains its existing semantics. Focused tests
verify capacity rejection, write failure, existing-name preservation, and zero
charged descriptors at rename. The existing exception and process-death
publication campaigns also pass with this ownership path.

### Rollback authority and recovery

The Store passes its registry through anchor directory traversal, authority lock,
record reads and writes, restoration receipt reads and writes, and snapshot digest
reads during enrollment and recovery. Each temporary writer closes before rename;
exclusive acquisition failures do not remove an existing staging name. Authority
and application locks remain held through the operation as before.

An existing anchored snapshot write reaches six simultaneous descriptors: the
application directory and lock, authority directory and lock, old snapshot, and
one temporary writer. The temporary snapshot and authority writers do not overlap.
The regression test rejects capacities zero through five without changing either
snapshot or authority, then uses six to exercise authority advancement followed by
an injected snapshot rename failure and subsequent recovery. It also restores an
older authenticated snapshot and regenerates a missing restoration receipt.
All successful and rejected operations leave no active descriptor reservations.
This does not complete migration-file, network, or native-memory accounting.

### Initial encryption migration

Migration now shares the Store registry for recovery-directory traversal, its
persistent coordination lock, existing recovery-copy reads, and new recovery-copy
staging. The staging stream closes before its owner closes and before hard-link
publication. Existing recovery identity, privacy, locking and durability checks
are preserved. The recovery directory and lock close before application publication.

The budget regression rejects capacities zero through four without changing the
plaintext source or publishing a recovery copy. At capacity five it creates an
encrypted recovery copy, survives an injected application-publication failure,
and retries through that existing recovery copy to commit matching ciphertext.
Both rejection and retry leave no descriptor owners or reservations. Existing
migration, rollback and publication crash campaigns remain part of verification.
SQLite, encryption scratch memory and networking remain outside this increment.

### Network integration constraints (pending implementation)

Network descriptors cannot use the file owner by simply calling os.close. The
HTTP response reader deliberately holds a socket.makefile reference after the
HTTP connection closes. CPython socket.close defers physical closure until those
references are gone. TLS wrapping transfers the same descriptor from the original
socket into an SSLSocket by detaching the original. The local socketpair probe in
validation/resource-lifetimes/socket-ownership confirms both properties on the two
qualification interpreters, without claiming TLS handshake coverage.

The next implementation must register and reserve before socket creation or
accept, preserve a single reservation across TLS transfer, and release only at
physical socket closure after the last reader. Failed creation releases unused
admission. Failed connection attempts release their own socket before trying the
next address. Ambiguous physical close must quarantine the charge without retrying
a raw descriptor number. Registry teardown must account for live readers rather
than reporting success from logical socket closure alone.

Outbound integration points are netio._Connection.connect, TLS wrapping, and the
_Response/_Reader lifetime. Inbound points are TCPServer listener construction,
accept, pending/active request ownership, shutdown_request and server_close.
Both paths require construction and transfer failure tests, budget sharing with
files/mappings, real reader-retention tests, and TLS handshake failure tests.
A hook on physical socket closure and explicit ownership transfer are needed;
releasing at HTTPConnection.close would contradict the observed lifetime.

files.atomic_write is currently called by compiler/CLI, transaction/evolution,
and trace export paths; its remaining scope must be assessed separately from
language core.file natives. Network TLS allocations and SQLite/encryption scratch
memory remain unaccounted. None of these observations complete issue 5.

### Socket primitive

resource_sockets.open_socket now registers a SocketOwner and reserves one shared
descriptor before initializing a socket. Its socket subclass hooks physical close,
so makefile readers retain admission after logical socket close. Registry teardown
reports incomplete while those readers remain. Acquisition racing teardown closes
the newly acquired socket and rejects its return. Ambiguous physical close retains
the owner and reservation and is never retried.

Focused tests cover reader retention, shared admission with a real file, invalid
socket construction, teardown during acquisition, and a close that physically
succeeds then raises. This primitive is not yet wired into HTTP. Raw detach is
rejected pending explicit TLS transfer; accepted sockets and TLS must be integrated
before network accounting can be claimed.

### Accepted sockets

The socket primitive now overrides accept with registration and descriptor admission
before the OS accept call. The Python socket shell also exists before acquisition.
A returned raw descriptor is held as pending ownership until socket initialization
succeeds; initialization failure closes it, retaining quarantine if close fails.
Accepted sockets use the same physical-close hook and reader lifetime as created
sockets. Default timeout/blocking inheritance follows CPython's accept behavior.

Loopback tests cover budget rejection leaving a connection queued for later accept,
reader-retained admission on the accepted socket, nonblocking accept failure, and
an injected initialization failure after the OS has returned a real descriptor.
The HTTP server has not yet adopted this primitive; TLS transfer is still pending.

### HTTP listener integration

The HTTP server constructs its listener through open_socket before binding and
uses the socket's admitted accept implementation. Listener and accepted clients
share the VM descriptor registry with files and mappings. Listener admission
failure maps to ListenError; bind failure closes the admitted listener. When accept
capacity is exhausted, the connection remains in the kernel backlog and the
coordinator pauses 10 ms before resuming its normal cancellation/shutdown checks.
This does not promise a 503 response to connections that cannot be accepted.

Focused tests exercise zero/one/two descriptor capacities, failed bind cleanup,
real request handling and handler reclamation, and zero descriptor usage after
server shutdown. Existing application-runtime cancellation, deadlines and worker
shutdown tests also exercise this integration. Outbound HTTP and TLS ownership
transfer remain pending; no TLS-memory accounting is claimed.

### TLS transfer and outbound integration

wrap_tls uses a per-transfer SSLSocket subclass with CPython's _create factory.
It does not mutate the shared SSLContext class. The TLS shell is registered as the
transfer target before the factory detaches the plain socket; detach changes the
owner's socket pointer without releasing or adding a reservation. Transfer requires
an open socket without makefile readers. Registry teardown reports incomplete while
transfer is underway; completed transfer checks closed admission before returning.
Validation and handshake failure close the current owner. TLS physical-close hooks
retain the charge while response readers remain, including after connection close.

netio now uses open_socket for each outbound connection attempt and wrap_tls for
HTTPS. The compiled outbound tests cover real verified TLS, stalled handshake,
framed response deadlines and cancellation, plus zero-capacity rejection before
socket construction. Their teardown asserts no pending socket owners or descriptor
charges. Primitive tests separately observe one charge across TLS transfer, reader
retention and cleanup after invalid hostname or failed handshake.

This integrates explicit inbound/outbound network socket descriptors. It does not
account for host resolver internals, trust-store file access, TLS native allocations,
or all native memory. Full combined qualification and the remaining resource scope
are still required before closing issue 5.

### TLS handoff failure and teardown

A failure after the TLS shell initializes but before source detach leaves two
Python objects naming one descriptor. Cleanup now detaches that temporary alias
before closing the original owner, preventing a delayed destructor from closing a
reused descriptor. The regression retains the temporary TLS object and verifies
its fileno is invalid after failure on either side of detach, with exactly one
physical close and no remaining reservation. The initial probe exposed an unclosed
TLS-object ResourceWarning before this correction.

Barrier-driven tests pause both before and after descriptor handoff while registry
teardown runs. Teardown reports incomplete and retains the charge; resuming the
transfer closes the socket, rejects its return, and leaves zero reservations.

### HTTP service credentials

Startup validation and per-request service-token reloads now pass the VM descriptor
registry through private-file traversal and reads. Admission exhaustion follows the
existing fail-closed configuration path at startup and returns 503 before handler
admission during reload. A live-server test leaves capacity only for accepting the
connection, verifies reload rejection without handler execution, then releases
capacity and verifies successful authentication. Shutdown leaves no descriptor
owners or charges. This accounts explicit token-file descriptors, not TLS trust
store internals or native allocation sizes.

### Request-body admission

The server reserves the declared body length against the VM native-byte ledger
before reading request content, after the existing framing and size checks. Capacity
rejection returns 503 and closes the connection without dispatch. The reservation
covers synchronous routing and handler dispatch; the request frame clears its body
reference and releases admission in finally. Zero-length requests reserve no bytes.
A live-server test exhausts byte capacity, verifies 503, then releases capacity and
verifies the same request succeeds. Existing malformed-body and cancellation tests
exercise cleanup paths. This is body admission accounting, not complete accounting
of parser buffers, decoded values, response serialization or traceback-retained
copies; those overlapping representations still require separate treatment.

### Request-body traceback lifetime

Dispatch now passes a shared request-body holder rather than raw bytes through
nested frames. The outer request finally clears the holder before releasing its
reservation. Retained exception tracebacks therefore cannot keep the admitted
raw body through those dispatch arguments. A live HTTP test injects a dispatch
exception, retains the server's actual traceback, checks both nested body holders
are empty, and verifies zero charged bytes. Decoded values and transport/parser
buffers remain separate accounting work.

### Encoder output cleanup

JSON text and byte encoding now close or clear their output accumulator in finally,
after copying the successful result or before an exception escapes. A retained
encoding traceback therefore does not keep the partial accumulator payload alive.
A regression retains size-failure tracebacks for both encoders and verifies their
output accumulators are closed or empty. This is cleanup preparation for shared
serialization budgeting, not admission accounting for every encoding temporary.

UTF-8 token locals are also cleared in finally. A stronger retained-traceback test
reproduced token-byte retention after size rejection in both encoder forms, then
verified cleanup without changing the conversion error. The original failure log
is retained during follow-up qualification. This does not charge encoding inputs,
escaped text or host allocator overhead against the resource ledger.

### Owned serialization payloads

ByteBuilder reserves UTF-8's four-byte-per-code-point upper bound before encoding
each token, then shrinks the reservation to its actual byte length. It retains
immutable chunks and their charges, reserves the simultaneous final joined payload,
and releases chunks only after joining or failure. BytePayload retains the final
charge until close clears its data. These are internal single-consumer owners;
callers must not retain exported bytes aliases past close.

encode_owned_bytes uses this builder for JSON byte output. Tests compare escaped
Unicode output with Python's independent JSON encoder, verify retained final-output
charges, reject insufficient final-copy capacity, and check failure cleanup. The
API is not yet wired into HTTP consumers. It accounts payload bytes, not Python
container metadata, string escaping temporaries or allocator overhead; the
four-byte admission bound can reject even when actual UTF-8 would be smaller.

### Owned HTTP response output

HTTP response encoding now uses encode_owned_bytes with the VM resource budget.
Encoding admission failure returns 503 before response headers are sent. The final
payload remains charged through telemetry, header emission and the body write;
context cleanup clears it on success or failure. The deadline writer clears its
bytes argument in finally so its exception frame does not retain an exported alias.
A live test verifies shared-capacity rejection, recovery after capacity release,
and the exact final payload charge during the response write. Input decoding,
escaping temporaries, HTTP header buffers and native transport allocations remain
separate accounting work.

Response-write failure tests now interrupt transport before any body bytes and
after a four-byte partial send. They observe the actual truncated HTTP response,
retain the server exception traceback, verify the payload was charged at failure,
and check the payload holder and writer argument are cleared before admission is
released. These tests establish cleanup for those injected boundaries; they do not
claim that a partially sent response can be retracted or retried transparently.
