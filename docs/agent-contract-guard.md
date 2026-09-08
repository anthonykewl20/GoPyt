# Protect approved rules during agent-written GoPyT changes

`gopyt-guard` is a developer tool for GoPyT projects. It keeps approved specs,
interfaces, acceptance cases, and the meaning of contract helper functions
outside the candidate implementation's change authority. The original `gopyt`
check/fmt/test/run commands retain their specified behavior.

The important distinction is between a compile-valid edit and an authorized
change to requirements. Ordinary checking can accept an agent changing both
copies of a contract. A pinned guard rejects that change. It also binds the
implementation of any function used by a contract, including transitive calls
across modules. Otherwise, changing a helper could silently change what an
unchanged postcondition means.

## Supported scope

The current guard supports dependency-free packages of pure functions, with
bounded i64/bool acceptance inputs and results. It captures at most 64 regular
files and 2 MB of package bytes. Candidate files comprise the manifest, derived
lock and spec/impl/test GoPyT sources; keep the review policy outside that tree.
This is not yet a general gate for effectful applications, dependency graphs,
record-valued acceptance inputs, or arbitrary repository layouts. Unsupported
projects are refused rather than labeled verified.

The operator controls the executor installation, approved bundle and expected
SHA-256 outside candidate write authority. A candidate-supplied CI script or
candidate-supplied expected hash does not establish approval. Protect those
inputs with an independently controlled runner and required checks. The tool
is not an operating-system sandbox against a compromised compiler or host.

## Prepare and review a baseline

Install the project normally to obtain `gopyt-guard`; `python3 -m gopyt.guard`
is equivalent when working from source. An external policy JSON contains:

```json
{
  "mutable": ["impl/inventory.gopyt"],
  "obligations": {"stock": "Reservation subtracts count from available stock."},
  "cases": [
    {"symbol":"inventory.reserve","args":[10,3],"expected":7},
    {"symbol":"inventory.reserve","args":[0,1],"trap":1}
  ]
}
```

The function and its spec must exist in the package. Prepare a new output file:

```sh
gopyt-guard prepare --candidate /path/to/approved-package \
  --policy /operator/policy.json --out /operator/bundle-v2.json
```

Review the resulting sources, requirements, vectors and contract-dependency
bindings. Preparation prints a hash but does **not** approve those bytes. Pin the
reviewed hash in operator-controlled configuration. Do not automatically replace
it with a hash computed from a submitted candidate.

```sh
gopyt-guard --candidate /path/to/candidate \
  --bundle /operator/bundle-v2.json --expected-sha256 REVIEWED_HASH \
  --receipt /operator/new-receipt.json
```

Zero exit status means the declared gate passed. Nonzero status is a refusal,
compile/execution failure, or failing acceptance case. Receipts cannot overwrite
existing evidence. The derived lock is regenerated in a disposable captured
copy; an old build or a candidate's test transcript is never acceptance evidence.

## Contract helper changes

Given `ensures result == formula.remaining(stock, count)`, the gate protects
`formula.remaining` and every application helper reachable from it. It uses the
compiler's resolved, instantiated call graph, not name matching. The baseline
binds helper body/contract ASTs, ordered signatures and resolved callees. The pinned engine covers
stdlib natives.

Comments and source positions can change. An AST-changing edit to a protected
helper requires a new operator-reviewed baseline, even if the author says it is
mathematically equivalent. The current tool is conservative; it is not an
equivalence prover. Bodies outside the contract dependency closure remain
editable, subject to the ordinary compiler, contracts and acceptance cases.

Bundle schema v2 requires explicit dependency bindings and validates the complete
bundle shape. Empty case sets, trap-only suites, duplicate JSON keys, mutable
spec authority, invalid hashes, and ambiguous outcomes are refused. Historical
v1 bundles must be reviewed and regenerated; they are not silently upgraded.

## What the evidence establishes

[Language-level R&D](agent-evidence/2026-09-08-language-guard/REPORT.md) covers
multiple domains, joint spec/implementation edits, transitive helper tampering,
bounded exhaustive execution and an inadequate-specification negative control.
The refund ledger is one integration fixture, not the language's product scope.

Under the stated trust assumptions, a protected contract cannot be changed by
an implementation-only candidate, including through its bound helper closure.
Runtime contracts then check executed calls. This is a conditional engineering
argument backed by tests, not a mechanically verified theorem about the VM.
Contracts can be incomplete, the accepted vectors can omit behavior, and a bad
implementation can refuse a valid request by trapping. Requirement correctness,
availability, effects outside this pure subset, and benefits to actual users
need separate evidence. The tool does not claim universal correctness or an
advantage over equivalently protected Go, Python or TypeScript workflows.

## Calibrating acceptance cases on observed data

The [VulcanBench real-data campaign](agent-evidence/2026-09-08-guard-calibration/REPORT.md)
compares equally sized source-order, hash-order and coverage-based case sets,
then tests new mutation predicates without retuning. A rare observed price escaped
all three small suites. Keep exact contracts and use every distinct development
input tuple when the corpus fits the guard's existing case and byte limits;
otherwise retain explicit coverage cases plus separately reported full replay.
Public data reused in prior experiments must not be called a sealed holdout.

[Calibration scripts](../benchmarks/language_guard/calibration/README.md) also
export a native VulcanBench task with a gold patch. Its hidden checks use an
external frozen engine and approved bundle. The weak-contract bundles in that
campaign are experimental controls. Preparing the stronger calibrated bundle
still does not grant operator approval or guarantee that every valid call completes.

## Typed acceptance and bounded execution

Acceptance cases must target an application pure function whose parameters and
return value are `i64` or `bool`. The guard checks exact argument count and types
against the compiled signature, including cases expecting a trap. A JSON boolean
is not an integer argument, and an invalid call cannot be accepted merely because
its arity error matches an expected runtime type trap. Expected return values must
also match the declared return type. Preparation rejects these errors early;
evaluation independently repeats the check for manually assembled pinned bundles.
Duplicate keys in the external preparation policy are refused as well.

Case execution uses the VM's cooperative cancellation checks with a monotonic
deadline token. It does not allocate a timer thread per case. The outer process
timeout and memory limit remain in force. Compiler-resolved dependency checks and
VM construction use the same checked program, retaining bytecode encode/decode
validation. Argument vectors are copied before entering the consuming VM API.

[Hardening evidence](agent-evidence/2026-09-08-guard-hardening/REPORT.md) records the
fixed custom VulcanBench regression score separately from model coding scores.
These changes alter the engine hash, so older pinned bundles must be reviewed and
regenerated. They are not silently migrated or approved.

Private helpers have no protected spec twin, so dependency bindings also include
ordered parameter names and types, return type, function kind and effects. This
prevents a parameter-order change from silently reinterpreting an unchanged
helper body. The hardening report retains a reproduced bypass of the earlier
body-only binding and its refusal under the final engine.

Preparation reads at most the policy byte limit plus one byte and validates the
bundle's structural limits before compilation. It also refuses preparation if
the engine source digest changes during the operation.

The shared VM now reuses one instruction guard per frame while acquiring and
releasing the heap lock at every instruction. Root scanning, collection,
contracts and telemetry remain active. Exact immutable scalar types take a
shorter adoption path. See the [VM and guard R&D evidence](agent-evidence/2026-09-08-guard-rd/REPORT.md)
for paired measurements and reference-heap comparisons.

For development investigation of missed branches, the optional
[boundary probe](../benchmarks/language_guard/calibration/BOUNDARY_PROBE.md)
compares authenticated baseline and candidate snapshots. It supports targeted
multi-argument searches and exhaustive comparison of an explicit finite input
domain. Its reports are diagnostics, not guard receipts or approvals; domain
completeness applies only to the declared tuples.
