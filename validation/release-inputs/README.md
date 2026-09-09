# Build-input and wheel verification increment

This advances #23 without closing its provenance, signing, inventory and response
requirements. See the normative release-input amendment for the selected inputs
and trust boundaries. No runtime source or GoPyT dependency was changed.

- `actions.json`: selected immutable action revisions and metadata blob IDs; Node24
  and checkout credential persistence were reviewed. Both workflows use the pins.
- `pypi-inventory.json`: selected versions, declared dependencies/licenses and exact
  published wheel URLs/hashes. Extras in upstream metadata are not adopted deps.
- `python-distributions.json`: queried GitHub distribution manifest, including the
  macOS gap that prevented using the initially considered 3.11.15 pin everywhere.
  The job distribution pins are in requirements/python-standalone.json instead.
- `setuptools-reference.json`: Leitir registry provenance, checksum verification,
  Git drift and license-routing limitations. Reference-only; no transplanted code.
- `install.log`, `install-3.11.log`, `editable.log`: hash-checked package installation
  in isolated local environments. No uncontrolled project build isolation.
- `hash-rejection.log`: an intentionally wrong package hash was rejected before
  installation, not accepted as a fallback or automatically replaced.
- `reproduction.json`, `reproduction-3.11.json`: two builds each under local Python
  3.14.7 and 3.11.15, at epoch 1788954760. All four wheels are byte-identical with
  SHA256 209aede8fb0e1385bfe4b7de6b3c08cb8d4801ffba680d3674cf3469bd43c7f9.
  These local versions are recorded exactly; the final CI target is 3.11.16/3.14.7.
- `verification-final.log`: wheel and interpreter negative controls for changed
  source, RECORD tampering, unexpected payload, duplicate/oversized inventories,
  wrong interpreter hash/size, archive traversal and mismatched executable version.
- `initial-full.log`: 752 language tests passed before the interpreter installer
  tests were added and the CI distribution gap was resolved.
- `full.log`: all 756 tests passed in 227.994 seconds with the hash-pinned
  build/security environment. Frozen source hashes matched after completion.
- `guard.log`, `stdlib.log`, `contracts.log`, `wheel-smoke.log`, `upgrade.json`:
  repository checks and installed old/new wheel rollback with the pinned backend.
- `source.json`: runtime/tests/build-tool/workflow/requirement hashes for validation.

The effective CI Python archives are a separate justified input, not a library
adopted from a Leitir source reference. The installer has actual hash and version
gates before PATH activation; positive platform qualification still requires the
final CI matrix. CI retains complete wheels and manifests for cross-platform
comparison rather than inferring equality from a green test count.

No throughput, real-data volume or production-SLO claim is made. The four local
wheel trials use exact frozen input/epoch identities and byte comparisons. Their
success does not establish reproducibility for arbitrary host versions, signed
publisher provenance, data rollback safety or universal correctness. Deliberate
negative controls are retained; unintentional failures, if any, remain visible.
