# HTTP worker startup cleanup

Runtime fingerprint: `b382bfb0025ed8af38ede3ce4cc97a584653ab35de57f512d43052014f65dc8e`.

The pre-fix regression (`before.log`) injects RuntimeError and OSError on the
first and second handler-worker starts. It observed uncaught RuntimeError,
an open listening socket and a surviving worker. Its finally block explicitly
reclaimed captured resources so the failing test did not leak workers itself.

The repaired compiled API fixture verifies ListenError, a reset serving flag,
joined workers, a closed listener, zero unfinished queue tasks and immediate
address reuse. It uses real worker threads and sockets. All 25 HTTP/scheduling
tests pass on Python 3.14.7 and 3.11.16; the complete 809-test suite passes on
Python 3.14.7. Guard calibration (17), stdlib parity (21 modules), contracts,
installed wheel smoke and upgrade/rollback also pass. Logs are retained here.

Two isolated builds at epoch 1788998400 produce the identical wheel SHA256
`00656dff14052c880d02e923dbd38dc57f19df89b27c5bd7d8dcd9f873c7799f`.
The CPython socketserver/threading reference and license hashes are recorded in
`source-reference.json`; no donor code was copied or executed, and no dependency
was added. This is deterministic fault-injection evidence for startup cleanup,
not a production load measurement or graceful-draining qualification.

See the [normative amendment](../../docs/parallel-admission-amendment-2026-09-10.md#http-worker-startup-failure).
Issue #13 remains open for its other original acceptance and evidence criteria.
