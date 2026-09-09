# Patched backend qualification

GitHub Dependabot alert 1 identifies setuptools <83.0.0 as affected by
GHSA-h35f-9h28-mq5c: Unicode-equivalent names can bypass MANIFEST.in exclusions in
source distributions. The selected backend is updated from 82.0.1 to the reviewed
84.0.0 wheel, with an exact SHA256 pin. No upstream extras are adopted.

`alert.json`, `pypi.json`, and `reference.json` retain the alert, publisher metadata
and reference provenance. The registry checksum is verified; Git parity drift
and inconclusive source license routing remain explicit. The wheel's declared
license is separate metadata. The already-selected backend remains a build input.

`exclusion-before-after.json` records a real source distribution: a harmless test
marker with an NFD name is included by 82.0.1 despite the NFC exclusion, and is
excluded by 84.0.0. The committed regression checks the resulting tar inventory.
This demonstrates the reported behavior without using real private data.

`install*.log`, `focused*.log`, `inventory31116.json`, `reproduction.json`,
`wheel-smoke.log`, `old-wheel.log`, and `upgrade.json` retain the installation,
reproduction and compiler installation/rollback checks. `wheel-difference.json`
compares the old and new backend outputs at the same epoch and distinguishes
metadata changes from unchanged runtime source. `source.json` freezes this
revision before `full.log`; earlier packaging-inputs evidence used 82.0.1.

The project release workflow emits explicitly allowlisted wheels, not sdists.
Recorded release wheels contain the expected runtime and metadata payload; no
private-file exposure was observed. This is an exposure assessment of the recorded
recipe, not a substitute for upgrading. The alert must be rechecked on GitHub after
the patch merges; it must not be dismissed merely because local tests pass.

The preceding packaging-only revision 09be464 passed both CI runs 34371358762/34371353924 with setuptools 82.0.1. Those results qualify that revision; the 84.0.0 security update requires new CI and must not reuse them as final-head approval.

Final local validation: 769 full tests passed in 259.696 seconds, frozen source hashes matched, seven focused packaging tests passed on Python 3.14.7 and 3.11.16, and reproduction, clean wheel installation, old/new/old compiler rollback, Guard and stdlib checks passed.
