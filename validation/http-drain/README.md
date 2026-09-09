# HTTP graceful-drain validation

Final runtime fingerprint: `faec9a9a10e6a9331c15ed9d316a2dee9848409118ac7277e197a5360b4c0b51`.

`before.log` retains the reproduced loss of an active response after its native
had already committed data. The repaired compiled API test reopens the real Store
to verify that commit, starts shutdown while the handler is held, then verifies
the complete 200 response and that the next pipelined handler never starts.

A real child-process SIGTERM test blocks the native after committing and printing
COMMITTED. A test observer invokes the actual installed signal handler and prints
DRAINING. Only after that marker does the parent release the native over stdin.
The response must complete, the process must exit successfully, and a reopened
Store must retain the value. This handshake avoids assuming that a fixed sleep
will overlap delivery of the signal. A separate test verifies that normal draining
does not extend a 200 ms active request deadline around a 30-second cooperative
sleep. Existing tests continue to check context cancellation/timeout aborts and
partial-input cleanup.

`worker-exit-before.json` retains a subsequent host fault: SystemExit in a native
left one queue item and one connection record after shutdown. Cleanup now owns
pending sockets even if a worker has exited, removes connection records in finally,
and uses bounded idle queue waits instead of possibly blocking sentinel puts.
The new regression exits a worker while another connection is queued and verifies
zero unfinished tasks, an empty queue, no connection records and a closed peer.
This trusted-host fault injection is not a claim that GoPyt source can call SystemExit.

The initial implementation passed 816 tests (`initial-full314.log`) before this
additional worker-exit gap was found. The `initial-*` build/check reports refer
to runtime `6830c992e25670ee9c0f42094b2649869e97874a8b1ff213703335ab6e2bbbe6`;
they do not qualify the later queue cleanup. The final 33 focused HTTP/scheduling
tests pass on Python 3.14.7 and 3.11.16. The final full suite passes all 817 tests
on Python 3.14.7. Guard calibration (17), stdlib parity (21 modules), contracts,
reproducible wheels, installed smoke and upgrade/rollback also pass. Two builds
at epoch 1788998400 match wheel SHA256
`e6550b376f8dea96b14c2aecb2cfefe1fe37096ce97e835aae087ec09e11953b`.

See the [normative amendment](../../docs/parallel-admission-amendment-2026-09-10.md#graceful-http-draining).
Normal shutdown permits active responses within existing deadlines; explicit
context termination aborts sockets and joins workers. Noncooperative natives may
still delay joining and commit before output fails. Peer delivery is not guaranteed.
These finite functional checks do not establish all-native shutdown behavior,
production latency or universal resource-leak freedom. Issue #13 remains open.
