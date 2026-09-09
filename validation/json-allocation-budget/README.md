# Ordinary JSON allocation ceiling

The existing per-value ceiling is 2,147,483,647 elements or bytes (implementer
trap 14). Ordinary JSON encoding previously accumulated escaped output without
checking this ceiling. The canonical encoder now counts UTF-8 output bytes
before appending. It keeps StringIO for ordinary text and the existing bytearray
path for separately bounded network encoding. The native and generated
`Json.to_json` convert allocation overflow to trap 14; CLI result serialization
reports E101 / trap 14, exits 2, and prints no partial JSON result.

## Regression evidence

`allocation-probe.py` compiles a real GoPyt package and calls its encoding
function. The same probe bytes produced `before.json` on runtime faea6f96 and
`after.json` on runtime fc6c1aa5. The probe temporarily reduces MAX_ALLOC to 16:
an exact 16-byte encoding succeeds, while independent `json.dumps` expectations
for 17-, 18- and 20-byte encodings require trap 14. Previously all returned
strings; afterward all three overflow cases trap. This is a reduced-limit policy
probe, not an actual 2 GiB allocation or peak-RSS experiment. Replay from the
repository root with `PYTHONPATH=. python validation/json-allocation-budget/allocation-probe.py`.

Four new regressions exercise exact escaped UTF-8 limits (including chunk
boundaries), canonical map/list output, rejection before oversized sorting or
escaping, compiled native/generated trait calls and recovery, and CLI trap/output
behavior. All 175 focused tests pass on Python 3.14.7 (12.268 s) and 3.11.16
(13.172 s). The final full Python 3.14.7 run passes all 874 tests in 316.585 s.
Runtime/test sources remained frozen throughout that run.

Guard calibration (17 tests), stdlib parity (21 modules), the contracts example,
installed-wheel checks and upgrade/rollback pass. Repeated builds are bitwise
identical: 46 runtime files, wheel SHA-256
`26671b4978ee16f96d4a17a5649747e98cd3e18ce96f4c43e3c24cf2b1572927`.
`build-report.json` records all 49 packaging-input hashes; all were rechecked
unchanged after the test-only correction below. Runtime SHA-256 is
`fc6c1aa55709c81e2c6560deb7d0e52a168c5ff5842aafd1f36df6ec41c6eada`.
`source-review.json` pins the probe and relevant final regression test sources.

## Failed trials and correction

The initial probe omitted a required GoPyt import; `initial-probe.py` and its log
retain that setup error. The final probe uses valid formatted GoPyt. The first
focused runs used stale example lock toolchain headers because a nonrecursive
lock-file search missed the nested guarded-refunds example; both initial logs
are retained. Refreshing all nine intended headers fixed those E040 failures.

The first full run (`initial-full314.log`) failed in an existing HTTP response
cleanup assertion during the diagnostic coverage suite's nested run. Receiving
the final response bytes does not synchronize with the handler's subsequent
`finally: release_result()`. The test now waits on an event emitted after the real
release operation, then asserts empty handoffs before another request, retaining
its live-server recovery and final cleanup checks. It does not sleep and assume
cleanup happened. Both focused suites and the full suite were rerun after this
test correction. The runtime did not change for that correction.

## Limits and scope

This enforces an existing per-value rule. It is not an aggregate heap/RSS limit:
Python object overhead, prior application graphs, sorting keys, decoding graphs
and output copies are separate. Ordinary conversion does not acquire the smaller
HTTP body/depth limits. Network-specific overflow remains its documented typed
error. Cancellation delivery and synchronous traversal boundaries are unchanged.
No dependencies or upstream source were added. Evolution disk retention and the
remaining issue #13 resource, cleanup and sustained-load criteria remain open.

The fresh `deadline-inventory.json` records source identity and native coverage;
it is not behavioral proof. Historical inventories remain immutable. Verify with
`python tools/check_deadline_inventory.py validation/json-allocation-budget/deadline-inventory.json`.

## Subsequent macOS CI test correction

The first PR60 matrix passed seven jobs but failed macOS 3.14.7 job
102665873406 (run 34411233334). The idle-connection regression gave a fresh
recovery request the same 150 ms test budget as the deliberately idle clients;
that request expired under runner load. `ci-macos-initial-failure.log` retains
this failure. The test now observes server-driven EOF on each still-open idle
client, then gives the fresh recovery request its normal ten-second input budget.
It preserves the idle closure and successful recovery requirements without an
assumed sleep or retries.

All 35 focused tests pass on Python 3.14.7 (10.308 s) and 3.11.16 (10.333 s).
A fresh full run passes 874 tests in 307.537 s, and the contracts example passes.
The runtime and all 49 packaging-input hashes remain unchanged, so the recorded
wheel/build/upgrade qualification still applies. `ci-correction.json` pins the
corrected test source. This update is test-only; original failed and successful
local trials remain retained above.
