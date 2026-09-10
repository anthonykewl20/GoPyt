# Socket ownership observations

Run `probe.py` with the two pinned qualification interpreters. The adjacent JSON
files record Python 3.11.16 and 3.14.7 observations from actual local socketpairs.
No external service, TLS handshake, or certificate validation is exercised.

Both plain and TLS sockets retain the OS descriptor after socket.close when a
makefile reader remains. Closing that reader physically releases the descriptor.
TLS wrapping detaches the original socket while preserving its descriptor number.
These are ownership observations, not completed budget enforcement or network
qualification. No descriptor-reuse race is injected by this probe.

Reference inspection: CPython commit
823f0323ee6ec1402088b73bce1a38473cac36dc, Lib/socket.py methods makefile,
_decref_socketios, _real_close, close and detach; Lib/ssl.py SSLSocket._create
and _real_close. Reference source was inspected through the existing Leitir
checkout; it was not imported or copied into the implementation.
