# Outbound HTTP budget validation

Runtime fingerprint: `e43bf4e4f40490035e85c73f66a61dee72a6962f579b80d5205c153e07d2a362`.

The original compiled HTTP/model probe (`before.py`, `before.json`) gave each
call a 100 ms VM deadline while a local server delayed its body for 300 ms.
Both calls waited until the body was released, returning timeout after about
302 ms. The same probe with a separate output path (`after.py`, `after.json`)
returns timeout at about 101 ms before body release. These single observations
reproduce the bug and repair; they are not a production latency distribution.

Eight new regression methods exercise real compiled outbound calls: held body
reads under inherited deadlines, cancellation and transport budgets; incomplete
headers, chunk-size extensions and trailers; HTTP error bodies; successful
chunked 200/404 responses; DNS that finishes after the deadline; stalled TLS
handshakes; trusted TLS and untrusted/wrong-host rejection; and a successful TLS
response after an observed polling timeout. Held-peer cases release and join
the peer during cleanup. Test certificates use the existing security extra.

Initial TLS tests failed because changing the fixture origin left its package
lock stale (`initial-tls-failed.log`). Refreshing that fixture lock repaired
those failures without changing certificate verification. The first broader
runs also encountered four failures from stale checked-in example toolchain
locks (`initial-lock314-failed.log`, `initial-lock311-failed.log`). Those locks
are refreshed; C030 preserves its deliberately invalid package digest.
The qualified 102-test focused suite passes on Python 3.11.16 and 3.14.7.
The initial full run found a generated policy transaction-lock file left by the
lock refresh, which Guard correctly rejected (`initial-full-failed.log`: 825
tests, 22 Guard errors and two dependent diagnostic-audit failures). After
verifying no holder and removing that generated file, the fresh full Python
3.14.7 run passes all 825 tests in 315.098 seconds. The contracts demo also passes. Guard calibration (17), stdlib parity
(21 modules), reproducible builds, installed wheel smoke and upgrade/rollback
pass. Two builds at epoch 1788998400 produce identical wheel SHA256
`17c1f94b8dc33fc01b80ab1c1c7345f277340573d21a00ed5d7eb10fe5380e8f`
with 46 runtime files.

The [normative budget policy](../../docs/parallel-admission-amendment-2026-09-10.md#outbound-http-and-model-budgets)
documents DNS and cancellation limits. DNS is still uninterruptible; blocked
connect, TLS and send operations can await their remaining socket deadline.
Response parsing polls cancellation without restarting its elapsed-time budget.
No automatic retries are introduced, and timeout does not imply remote rollback.
These finite tests do not qualify all native I/O, global fairness or production
resource/latency requirements. Issue #13 remains open.

`source-reference.json` records CPython source identities consulted for HTTP
parsing, descriptor ownership and TLS behavior. No source was copied or executed
from the reference shelf, and no target dependency was added.
