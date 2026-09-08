# Compiler context and check receipts

The developer tool `python -m gopyt.project_context` exports checked project
interfaces and records actual compilation/test results. It is separate from the
locked `gopyt fmt/check/test/run` CLI and changes no language syntax or bytecode.

From the repository root (choose a new output path for every execution):

```bash
python -m gopyt.project_context context examples/orders --output /tmp/orders-context.json
python -m gopyt.project_context test examples/orders --output /tmp/orders-tests.json
python -m gopyt.project_context verify examples/orders /tmp/orders-tests.json --output /tmp/orders-replay.json
python -m gopyt.project_context diff /tmp/orders-context.json /tmp/orders-after.json --output /tmp/orders-contract-diff.json
```

`check` also produces context but runs no tests. `context` and `check` both perform
full static checking, emission and validating bytecode decoding. `test` performs
those operations and executes root-package tests in CLI order in one VM. They use
the compiler/VM directly, not a simulated command log. Their exit codes are 0 for
completion, 1 for compilation/tooling failures, and 2 for a runtime test trap.
No source repairs, lock updates or build artifacts are written to the input tree.
Transaction lock/recovery files follow the compiler's existing advisory protocol.

Every output path must be new: completed receipts and failures cannot be silently
overwritten. Keep receipts outside `spec/`, `impl/`, `test/` and dependency source
trees. Failed compilation contains diagnostics and no successful context or stale
artifact claim. Tests report their planned names, each attempted result, and an
explicit `not_run`, `no_tests`, `passed`, `failed`, or `error` status. A first trap
leaves subsequent tests unexecuted. Zero tests is never described as passing tests.
A test can pass while business acceptance remains `not_evaluated`.

## What context contains

Schema `gopyt.project-receipt.v1` contains public declaration text derived from
checked parser nodes, generic signatures, types/fields/variants, trait/provide
members, requires/ensures clauses, agent declarations, HTTP routes and egress.
Each declaration links to its input file, module and line. Ordinary comments are
excluded from declaration comparisons; they remain part of the source identity.
Declarations are not paraphrased into inferred business requirements.

Module imports include their allowlists and source location. Package metadata and
direct dependency relationships include transitive packages. Concrete function
signatures/effects and resolved call edges come from semantic checker data;
parallel-arm and framework HTTP dispatch edges are distinguished. Uninstantiated
generic bodies retain declarations but have no concrete call edges. An imported
stdlib call has its resolved signature in `functions`; full stdlib declarations
remain in the shipped language reference. This is not a complete dynamic call
trace or a claim that all business requirements are represented by contracts.

`diff` requires two successful compilations and reports added, removed and changed
public declarations. Line-only movement or ordinary comment changes do not count
as contract edits. Text differences identify review targets; the tool does not
infer backward compatibility, semantic equivalence, or intended business policy.

## What a receipt proves and what replay checks

The tool copies the complete manifest/source/lock dependency graph to a temporary
tree and compiles those copied bytes. Source identities hash all regular files in
spec/impl/test, including non-language files and dependency tests, plus manifests
and any locks. The artifact hash covers bytes actually emitted and decoded during
that execution. It never accepts an existing `build/out.gobyte` as evidence.
Working-tree edits after capture cannot change the copied program under test.
Post-execution source changes inside the copy make the operation fail.

Tests execute at a fresh temporary package root: persistent DB/file fixtures from
the original application are not copied. Tests must initialize their own state.
Tests retain their declared effects, so network/environment/time/random behavior
is not isolated, frozen, or necessarily reproducible. Use ordinary `gopyt test`
when deliberately testing existing package-local state. The receipt explicitly
states this execution scope.

`verify` checks the receipt content hash, recaptures current input, and re-executes
the recorded operation. It compares source and engine identity, emitted artifact,
context, compilation and per-test outcomes. It retains new stdout/stderr but does
not require matching effectful output text. A replay can match a recorded failure:
inspect `replayed_exit_code` and the underlying checks. A source/lock/dependency
change, changed test outcome or modified receipt fails comparison. Identity-free
capture failures cannot be verified as source-bound executions.

These hashes are integrity checks, not signatures or attestations. Anyone who can
rewrite a receipt can recompute its digest. Replay provides a new local execution;
it does not prove who ran the historical operation or authenticate its statements.
Engine identity fingerprints on-disk compiler Python sources and Python version,
not the interpreter executable, native libraries, or already-loaded bytecode in a
long-lived embedding process. Prefer fresh CLI invocations. Per-package advisory
locks protect cooperating writers; capture is not an atomic multi-package snapshot
against arbitrary external editors. Copied graph consistency must pass the root
compiler lock check. No production, adversarial-isolation, agent-effectiveness or
runtime-performance conclusion follows from a receipt.

## Peon delegated use

[Peon delegation v2](peon-delegation-v2.md) consumes these compiler/VM receipts on source-bound candidate copies. Its grants currently cover standalone packages only and refuse path dependencies. This restriction belongs to the delegation protocol; the project-context API retains its existing package-graph contract.
