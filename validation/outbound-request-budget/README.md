# Outbound request preparation bounds

The compiled real-HTTP probe sends a 1,048,577-byte POST body. Before the change,
the server receives it and returns 200. Afterward the native returns `HttpError`
with `body too large` and the server receives no request. Scripts differ only in
report filename. Runtime/script hashes and observed server lengths are retained.

Five regressions cover the actual 1 MiB HTTP/model payload boundary, model-envelope
accounting, independent escaped UTF-8 JSON expectations, 8,192-byte URL boundaries,
rejection before transport, and own-budget/VM-deadline/cancellation delivery during
model serialization. The serialization test advances the deadline clock at the
actual encoding boundary; it does not assume wall-clock scheduler timing. All
148 focused tests pass on Python 3.14.7 (14.015 s) and 3.11.16 (14.712 s).

All 870 full regression tests pass on Python 3.14.7 in 315.822 seconds.
Guard calibration (17 tests), stdlib parity (21 modules), the contracts example,
reproducible wheels, installed-wheel checks and upgrade/rollback also pass. Two
builds are bitwise identical with 46 runtime files; build-report.json records the
packaging inputs. Runtime, test and build sources stayed frozen during the full run.

The [normative policy](../../docs/parallel-admission-amendment-2026-09-10.md#outbound-request-preparation-bounds)
limits native request payload and URL admission. Pre-existing application values,
bounded temporary copies and aggregate process memory are separate. JSON response
decoding and OS operations retain their existing synchronous boundaries. Restricted
egress, TLS verification, no ambient proxies/redirects and no automatic retry remain
in force. These tests do not qualify hostile local providers or complete issue
#13's sustained resource/fairness/shutdown requirements.
