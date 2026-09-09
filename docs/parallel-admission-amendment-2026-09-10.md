# VM-wide parallel admission and inherited deadlines

This normative amendment advances architecture #13. It extends the existing
per-group `parallel max` limit with a shared per-VM worker reservation and
propagates an absolute monotonic deadline through structured workers. It does not
complete the issue's blocking-I/O, end-to-end admission or graceful-drain scope.

## Worker admission and overload

A VM allows at most 64 simultaneously reserved parallel worker slots. A trusted
host may lower this through `VM(..., parallel_workers=N)` for integer N in 1..64.
Every group atomically reserves `min(max, arm_count)` slots before starting any
worker. If unavailable, it traps with code 7 (`TRAP_PAR_MAX`) before any arm in
that group runs. Capacity is shared by nested groups and concurrent host calls
on the same VM. Existing parent workers keep their reservations while joining
children; a nested group cannot wait forever holding all capacity because
admission is fail-fast, not a queued capacity wait.

Local arm scheduling remains by source index under the group's scheduling lock.
There is no global wait queue, retry, FIFO/starvation guarantee or admission
reservation for a future request. Overloaded callers decide whether and when to
retry; no automatic retry can duplicate an effect. Separate VMs have separate
budgets. Host threads, HTTP worker threads and foreign/native internal threads
are not parallel-worker slots and remain separately bounded or host-controlled.
This is not an aggregate process-memory or all-task-queue guarantee.

All successfully started threads are joined before capacity is released, including
partial thread-start failure, trap, cancellation and coordinator exceptions.
Thread-start resource failure maps to trap 7 after cleanup. Other native exceptions
retain their existing behavior after cleanup. A group rejection does not undo
already-started work in an enclosing group. HTTP handlers return empty 503 for
trap 7, empty 504 for timeout trap 6 and empty 500 for other traps. These responses
do not imply that an enclosing operation made no durable change.

## Deadline propagation and cancellation

`VM.deadline_ns` is a host-controlled, thread-local absolute monotonic-nanosecond
deadline, or None. Hosts restoring a reused calling thread must restore their
previous deadline explicitly. GoPyt cannot assign this property. Each parallel
group computes the minimum of its own timeout deadline and the inherited deadline;
workers receive that exact minimum alongside ancestor cancellation, resource
authority and request identity. A child cannot extend an ancestor's budget.
The calling coordinator's deadline is unchanged by the child group.

The VM checks cancellation/deadline at call entry, after calls, and at bytecode
instruction boundaries. Both sleep APIs check the same state between bounded
host waits. Worker scheduling also stops on cancellation/deadline. A deadline
expires with trap 6. Explicit ancestor cancellation remains cancellation. Timer
representation is integer nanoseconds; host joins/sleeps use waits no longer than the remaining shared budget or
50 ms, whichever is smaller, and recheck state. This is a cooperative delivery rule, not a guarantee of
50 ms physical scheduling latency.

A native that blocks without polling still delays join. The parent requests stop
and joins it before returning; it does not abandon a writer. A native may publish
before the timeout/cancellation is reported. Use the [transaction outcome and
receipt rules](transaction-outcomes-amendment-2026-09-09.md) to reconcile ambiguity;
never infer rollback from a missing return value. Existing network/DNS/filesystem
blocking boundaries do not yet consume this budget uniformly. HTTP input, queue,
execution and response share the per-request budget defined below.
HTTP draining is defined below; broader shutdown and sustained all-native leak
qualification remain open in #13.

## Validation and reference scope

Compiled regressions cover over-budget zero-start rejection, nested and concurrent
admission, deadline inheritance, partial thread-start failure, ancestor cancellation,
HTTP 503 recovery/504 timeout, and a deliberately noncooperative native that must
finish before timeout returns. The latter is retained as a limit demonstration,
not hidden as a passing low-latency case. The pre-change reproduction reached
64 simultaneous native calls with three nested `parallel max 4` levels; each local
limit was respected, but no shared worker cap existed.

CPython `threading.Thread.start`/join behavior was read from the sampled-verified
Leitir reference `python/cpython@823f0323ee6ec1402088b73bce1a38473cac36dc`,
`Lib/threading.py`, and its PSF license history. No donor code was copied or
executed, and no runtime dependency was added. Runtime tests run against the
installed supported Python interpreters.

The [retained validation report](../validation/parallel-admission/README.md) includes
frozen workload identities, actual latency/resource observations and the initial
trial that motivated the sleep-wait correction.

## HTTP worker startup failure

If an HTTP handler worker cannot start, the server joins every worker already
started and closes the listening socket before returning ListenError with
`worker startup`. The VM's serving flag is reset so a later attempt is possible.
No request is admitted before the pool has started. Unexpected initialization
exceptions also reclaim initialized resources and reset serving state before
propagating. Cleanup does not join an unstarted thread. This startup guarantee
does not complete graceful draining of an already serving application.

## Serving context cancellation

The HTTP serving coordinator observes its caller's cancellation and absolute
deadline on each accept-loop service tick (100 ms requested polling interval).
It closes a newly accepted connection without queuing it if that context has
already expired. Handler workers inherit the serving context and observe server
stop alongside ancestor cancellation at VM/native cooperative checks. These
are service-lifetime limits, distinct from the per-request budget below.

Context termination leaves the accept loop through its normal cleanup path,
closes sockets and joins all handler workers before propagating cancellation or
timeout. Partial-input clients are interrupted by socket shutdown. Cooperative
native handlers stop; noncooperative natives still delay joining and may commit
before an error reaches the caller. This is not a graceful response-delivery
or OS wakeup-latency guarantee. A timeout response may be lost when the serving
context closes its connection; durable outcome reconciliation remains necessary.

## Per-request budget

The HTTP request timeout is ten seconds total across queue residence, input,
cooperative handler execution and response writes. The first request's absolute
monotonic deadline starts when the accepted connection reaches request admission,
before taking the connection-set lock or entering the bounded queue. An expired
queued connection closes without invoking a handler. Each later keep-alive
request receives a new ten-second budget beginning when the worker starts waiting
for it; idle time counts. Every budget is capped by the serving deadline.

The same deadline is installed in the worker VM context for parsing/dispatch and
response generation, then restored afterward. Input and response writes consume
remaining time; output backpressure cannot reset the budget on each write.
Handler dispatch preserves ancestor cancellation. Expiration of the whole request
closes the connection; it does not promise a 504 response after the output budget
has expired. A shorter nested parallel timeout can still return 504 while the
request budget remains available. Queue-full rejection remains 503.

Noncooperative native work can still exceed the budget before joining. Requests
are not retried by the server, and missing/partial responses do not prove rollback.
These finite time and queue bounds do not establish fairness among keep-alive
connections, a bound on arbitrary handler-generated values, or graceful draining.

## Graceful HTTP draining

Normal in-process server shutdown and SIGINT/SIGTERM begin draining. The listener
closes, queued and incomplete-input connections close, and no new application
handler starts, including pipelined requests on an existing connection. A request
becomes active after its complete input and route/authentication checks, at the
atomic dispatch admission boundary. Active requests may finish and write their
responses within their existing request/serving deadlines. Draining does not
extend those budgets. Workers are joined before the serving task returns.

Explicit serving-context cancellation, serving deadline expiry and unexpected
coordinator exceptions retain abort behavior: request cancellation, close sockets,
then join workers. SIGINT/SIGTERM merely mark draining; the coordinator observes
that state on its service tick without needing to allocate a shutdown thread.

A noncooperative native can delay final joining even after its request deadline.
It is never abandoned to continue writing after the serving task returns. A peer
may disconnect or an output deadline may expire after a durable commit, so receipt
reconciliation still applies. Draining offers a chance to deliver an active
response; it cannot guarantee delivery to an unavailable peer or undo a commit.
This HTTP lifecycle policy does not complete all native/resource shutdown scope.

Worker termination also removes its connection record in a finally block.
Shutdown discards queued sockets itself and joins all started workers, including
workers already terminated by an unexpected native exit. Idle workers observe
draining through bounded queue waits, so shutdown never blocks trying to enqueue
a sentinel into a full queue whose consumers have exited.

## Outbound HTTP and model budgets

`net.http.request` and the remote `core.model.complete` transport each receive a
30-second elapsed-time budget, capped by the calling VM's absolute deadline.
DNS resolution, connection attempts, TLS negotiation, request writes, response
headers, chunk framing and response bodies all consume this same budget; progress
does not reset it. Response reads poll cancellation with a requested maximum
50 ms socket wait. Socket ownership remains live until the response closes, and
polling timeouts preserve buffered HTTP parsing and TLS state.

Host DNS resolution remains uninterruptible. Its elapsed time counts, and an
expired context cannot start a subsequent connection attempt. Connect, TLS
handshake and each bounded request write use the remaining deadline; explicit
cancellation is checked around these operations, but cannot interrupt an already
blocked operation sooner than its socket timeout. These are cooperative bounds,
not OS scheduling or DNS latency guarantees.

Inherited cancellation and deadlines retain VM cancellation/timeout semantics.
Exhausting the transport's own budget returns `HttpError` or `ModelError` as
appropriate. HTTP error-status response bodies consume the same read budget;
remote model error statuses close their responses without consuming the body.
The existing 8 MiB response cap, source/operator egress intersection, disabled
ambient proxies and redirects, and default TLS certificate/hostname verification
remain in force. No request is automatically retried. A timeout or missing
response cannot establish that the remote operation did not commit.
