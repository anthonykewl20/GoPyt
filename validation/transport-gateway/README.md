Transport, gateway and egress boundary evidence

Frozen source/runtime identity is in source.json (runtime
sha256:66d9349fe5764e4d965bc168667e2d22f51e3d9ed230e41c7e4b0bf93c459d1f).

Python 3.14.7 full language suite: 1,161 tests passed in 504.675s (full314.log).
Python 3.11.16 full language suite: 1,161 tests passed in 548.945s (full311.log),
with one test skipped because that run used an exported copy with no Git metadata.

Two wheel builds under SOURCE_DATE_EPOCH=1788998400 are byte-identical
(wheel.log, wheel-report.json; wheel SHA256
4f132cf10cdd6487f046d13f24eb398304e39659bda80a67099095789a598996, 62 runtime
files). Installed-runtime smoke, format 2 to format 3 upgrade and rollback,
22 Guard tests, 22-module stdlib synchronization and all four contract-demo
outcomes passed.

full311-initial-failures.log retains the first Python 3.11 run, which failed.
Its four failures were not a transport defect and not a Python version
difference: the adaptation-inventory check merged in #82 ran `git ls-files` and
raised a subprocess error in an exported checkout with no Git metadata, and the
two diagnostics-coverage tests depend on the rest of the suite passing. The tool
now falls back to walking the same file set, and a test asserts the walk and the
tracked listing agree wherever Git metadata exists.

The gateway tests run through a real TLS terminator in front of the loopback
service. They exercise a served request end to end, an untrusted certificate,
gateway loss, a service restart behind the surviving gateway, connection
exhaustion and recovery, a missing required identity, a duplicated forwarded
header, and per-identity admission. Certificates are generated per test into a
temporary directory and are not retained.
