# HTTP request input deadlines and framing

HTTP connections enable TCP_NODELAY. A real persistent-connection probe found
that separately written headers and small bodies otherwise triggered roughly
40 ms Nagle/delayed-ACK stalls on the test host. This applies to all served
connections; benchmarks use the shipped server configuration.

Application validation found that idle connections could retain every worker,
and truncated Content-Length bodies could reach handlers. This amendment adds
an absolute ten-second request input deadline covering the request line,
headers, and body. A persistent connection receives a new deadline for each
request. Sending occasional bytes does not extend the deadline. Expiry closes
the connection; no application handler is dispatched for incomplete input.

The server rejects a body shorter than its declared Content-Length with HTTP
400 and closes the connection. This includes requests whose route does not use
a body. Existing duplicate-length, chunked-transfer and body-size rejection
rules remain in force. Socket writes have a ten-second timeout after the body
has been read. This is not a total execution deadline for application handlers
or a hard wall-clock bound on a response streamed to a slow reader.

The existing 64-worker and 1,024-queued-connection limits remain. Waiting in
the queue precedes a worker's request deadline; queue residence has no separate
deadline. Queued or active sockets are interrupted on server shutdown.
These limits bound concurrency, not guaranteed service under sustained attack.

Real-wire tests in `gopyt/test_app_runtime.py` cover truncated bodies, framing,
malformed JSON, pipelining, idle/drip-fed clients, queue pressure, recovery and
shutdown. Tests use reduced worker/deadline limits for reproducibility.
