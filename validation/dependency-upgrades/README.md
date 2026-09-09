# Dependency, installed upgrade and rollback qualification

This extends issue #22's exact toolchain binding with path-graph migration and
installed-wheel evidence. No additional runtime behavior or dependency is changed.
The full original issue criteria are mapped in acceptance.json; merge/CI and
tracker updates remain publication gates.

## Dependency graph cases

`gopyt/test_dependency_upgrades.py` builds a three-package graph: the application
imports prices, which imports rates. The initial result is 8. A transitive rates
upgrade changes the result to 10 only after explicit canonical lock replacement
and successful recompilation. Before acceptance, E041 leaves both the prior lock
and build output unchanged. Restoring the old dependency source and old lock
reproduces the original bytecode exactly and returns 8.

Other cases show that keeping the dependency version unchanged does not hide a
source change; changed postconditions are pinned by the lock and enforced by the
rebuilt VM; accepting a lock cannot make an incompatible dependency API typecheck;
and relocating the complete sibling graph preserves lock text and artifact bytes.
These are exact state and byte comparisons, not timing/performance claims.

A retained artifact is a compiled program snapshot. An embedder explicitly
selecting that artifact continues to run its old program after unrelated disk
source edits. `check`/`run`/`test` validate source locks before producing/executing
the requested source build. The runtime fingerprint binds compiler/VM semantics,
not live application source paths; deployment must select a matching reviewed
source/lock/artifact unit. No automatic migration or header rewriting occurs.

## Installed old/new pair

`tools/upgrade_smoke.py OLD.whl NEW.whl` creates a fresh temporary virtualenv,
installs only the provided wheel with `--no-index --no-deps --force-reinstall`,
then performs old → new → old installs in that environment. Each phase runs in
an isolated interpreter outside the checkout and verifies that imported runtime
files are beneath that environment. The test function returns 42 on valid input
and traps 1 on a failed precondition in every matching phase.

The new runtime rejects old format-2 bytecode with E100 and an old lock with E040,
leaving its bytes unchanged; explicit lock repair/rebuild produces format 3.
Rollback reinstalls the old wheel, rejects the new artifact, restores the old
lock and reproduces the exact original artifact bytes and behavior. Both wheel
SHA-256 values and all three runtime IDs/formats/results/artifact hashes are
recorded in installed-pair.json (Python 3.14.7) and installed-pair-3.11.json
(Python 3.11.15).

The old wheel is built from actual Git commit
710152358087dcf0b7e171f22a7f9a0eed557a01, the last merged format-2 source baseline.
It is a pinned development baseline, not a fabricated published release. The new
runtime identity is recorded in source.json. CI fetches that exact historical
commit, builds both wheels locally, and runs this same installation/rollback
sequence on Linux/macOS with Python 3.11 and 3.14. Build-toolchain provenance,
reproducible wheel ZIP metadata and future release signing remain #23 work.

These tests qualify the explicit incompatibility/rebuild policy for the named
pair; they do not claim arbitrary historical/future version interoperability,
universal semantics, or data-schema/encryption rollback safety. Matching runtime
source fingerprints remain mandatory. Application data migration, deployment
rollout and disaster-recovery qualification remain separately tracked.

## Validation

- focused.log: five dependency tests passed on Python 3.14.
- focused-3.11.log: eight compatibility/dependency tests passed on Python 3.11.
- installed-pair JSON/logs: clean install/upgrade/rollback on both interpreters.
- old-wheel-build.log: retained baseline build output.
- full.log: all 747 language tests passed in 236.324 seconds.
- guard.log, stdlib.log, contracts.log: remaining local validation checks.
- source.json: exact runtime/tests/harness/workflow hashes captured before full
  validation, with interpreter/platform and historical source identity.

All trials in this increment passed at first execution. Previous compatibility
migration/fixture failures remain retained in validation/toolchain-compatibility.
No performance or production-data result is asserted. Source identities and
independent exact-result checks are retained; finite success is not universal
correctness or an independent security audit.
