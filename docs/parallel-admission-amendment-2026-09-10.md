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
execution and response deadlines are not yet one end-to-end request budget.
Graceful draining and sustained all-native leak qualification remain open in #13.

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
