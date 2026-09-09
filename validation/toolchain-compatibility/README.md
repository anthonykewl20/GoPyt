# Exact toolchain identity increment for issue #22

The runtime now binds format-3 bytecode and canonical lock IDs to the exact
installed runtime source fingerprint. This is an intentional compatibility break:
old formats require source review and recompilation. It is not release signing.
The normative toolchain compatibility amendment records the algorithm, identity
limits, explicit lock repair, dependency review, rebuild and rollback procedure.
Issue #22 remains open pending the rest of its upgrade/dependency qualification.

`gopyt/test_toolchain_compatibility.py` checks matching header/source identities,
round trips, rejected older/unknown formats and mismatched fingerprints, direct
host artifact checks, identical source copies, and changed-source rejection in
an isolated Python process. A separate runtime copy has one comment appended to
ops.py, producing a different identity without changing its numerical semantics.
It must reject the original artifact with E100 and lock with E040, preserving the
old lock bytes. An explicit operator lock replacement/rebuild succeeds; the
original runtime rejects that rebuilt artifact but still executes its own prior
artifact. This models exact compatibility rather than claiming semantic equivalence
across different runtime identities.

`historical.json` additionally records a real format-2/format-3 compiler/runtime
matrix against Git revision 710152358087dcf0b7e171f22a7f9a0eed557a01. Its gopyt
sources were exported with git archive into an isolated temporary directory.
A pure `demo.answer` function returns 7 under both matching compiler/runtime pairs.
Both cross-version artifact loads reject E100. The new compiler rejects the old
lock with E040 without changing it, and explicit lock replacement/rebuild returns
7. Artifact SHA-256 values are retained. This narrow historical probe establishes
format rejection and explicit rebuild, not whole-language semantic compatibility
or a deployed release rollback certification.

`identity.json` records Python/platform, the toolchain ID, runtime and test source
hashes, and byte-for-byte equality of the wheel's 42 runtime Python files with
the checkout. The wheel excludes tests; those are also excluded from the runtime
fingerprint. The clean-wheel smoke test separately executes compiler, VM, native
transaction, Guard and tooling entry points outside the source checkout.

Retained trials:

- `initial-existing.log`: the old auth example lock correctly failed E040 during
  initial existing-suite validation. All eight example locks were explicitly
  regenerated for the chosen source fingerprint.
- `initial-fixture.log` and `initial-empty-task-effect.log`: new test fixtures were
  rejected for missing/empty task effects. The pure computation was corrected to
  a `fn`; no language rule was relaxed.
- `focused-new.log`: the three new compatibility tests pass.
- `focused-3.11.log`: 207 VM/error/repair/compatibility tests pass with the existing
  security extra cryptography 50.0.1 on Python 3.11.15.
- `full.log`: all 742 language tests passed in 280.091 seconds on Python 3.14.7; source hashes remained unchanged.
- `guard.log`, `stdlib.log`, `contracts.log`, `wheel.log`, `wheel-smoke.log`:
  repository validation gates.

The source identity is a compatibility discriminator, not publisher authenticity,
a sandbox, or a dependency/OS inventory. No donor code or new dependency was
adopted, and no throughput, real-data or production-SLO claim is made. Dependency
upgrade cases, broader supported host matrices and release provenance remain
separate required work; finite tests do not establish universal correctness.

The first full run (`initial-full.log`) exposed two migration-fixture issues:
C030 still carried the legacy toolchain label, masking its intended E041 stale
digest with E040, and lock regeneration left a generated transaction-lock file in
the Guard policy directory. C030's label was updated while preserving its zero
(stale) digest. The generated file was removed after the run ended; Guard's
package-file admission rule was not relaxed. The focused Guard/conformance rerun
is retained in `guard-fixture-regression.log` before repeating the full suite.
