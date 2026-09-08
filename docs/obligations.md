# Business obligations, verification scope and change context

`python -m gopyt.obligations` is developer tooling layered on
[project context](project-context.md). It changes no language syntax, bytecode,
lock format or locked CLI command. It exists because the reliability experiment
showed the counterexample it must address: **`gopyt check` accepts a well-typed
implementation that violates a prose business rule**, and a green test run says
nothing about which requirements those tests reached.

The tool has three parts:

1. A **registry** (`obligations.json` at the package root) giving every business
   requirement a stable id, an authority level and zero or more representations.
2. **Semantic facts** derived from the checked program: per-function body identity,
   record-field writes with their syntactic source, field reads, enum comparisons,
   resolved call edges and field coupling (writer of a field -> readers of it).
3. **Reports** that bind representations to facts and to retained evidence, and an
   **impact** view that says which obligations and evidence a change touches.

Nothing here is a proof. Read every status as “established for the inputs that
were actually executed, against the sources whose hashes are recorded”.

## Registry

```json
{
  "schema": "gopyt.obligations.v1",
  "contract": {"path": "../../benchmarks/order_lifecycle/CONTRACT.md", "sha256": "sha256:..."},
  "obligations": [
    {"id": "ORD-REF-001", "title": "...", "authority": "contract",
     "source": "CONTRACT.md, refund", "prose": "...",
     "functions": ["orders.refunds.refund"], "state_fields": ["refunded_cents"],
     "representations": [
       {"kind": "ensures", "function": "orders.refunds.refund", "text": "<clause exactly as formatted>"},
       {"kind": "preserved_field", "type": "orders.model.State", "field": "shipping_cents",
        "except": ["orders.placement.place", "orders.model.initial"]},
       {"kind": "test", "test": "test.orders.service.split_refund"},
       {"kind": "acceptance", "suite": "order_lifecycle", "report": "report.json"},
       {"kind": "prose", "text": "..."}]}]}
```

Ids must be stable, upper-case, dash-separated (`ORD-REF-001`). Authority is one of:

| authority | meaning |
|---|---|
| `contract` | part of the application's frozen contract; the application must satisfy it |
| `proposed` | a maintenance policy under discussion; **not** current behavior, listed so the gap is explicit |
| `unresolved` | requested behavior whose policy is undecided; **no executable representation is allowed** (the registry loader rejects one) |

Representation kinds and what each can establish:

| kind | checked how | establishes |
|---|---|---|
| `requires` / `ensures` | clause text must equal a contract on the named spec function after formatting, **and** the function must have an instantiation in the compiled program | typechecked and compiled; enforced by the VM on **every executed call** (trap 1/2 on violation). A bound clause whose function has no instantiation (an uninstantiated generic) is `typechecked only (no instantiation in this build)`: no runtime-enforcement claim is made, and the gap says the clause is neither compiled nor enforced in this build |
| `preserved_field` | every compiled constructor of the record type outside `except` copies the field from a parameter of the same type | syntactic preservation across all constructors; values that are computed are reported `not_established`, not assumed |
| `test` | the named test exists, its result in a **current** test receipt, and whether its call closure reaches an obligated function | behavior on the inputs the test used; a passing test that never reaches an obligated function is flagged, not counted |
| `acceptance` | an external report carrying a versioned evidence schema, a suite identity, a `passed` outcome and a `source_before` file inventory; the inventory must cover **every** current package file and every compiler file with matching digests, and the outcome must agree with its own scenario counts and failures | independent corpus result bound to the complete current inputs; incomplete coverage stays `partial`, a `passed` claim contradicted by its own counts stays `contradictory`, hash mismatches stay `stale`, and none of these is ever reported as passing |
| `prose` | nothing | nothing; the obligation is reported as unverified |

Verification scope is reported per obligation as `static`, `runtime_contract`,
`tests`, `acceptance` and `prose_only`, plus a list of `gaps`. Gaps include
missing clauses, violated or unestablished preservation, tests that were not run,
failed, stale, or never reach the obligated code, stale or unsupplied acceptance,
proposed policies, and unresolved decisions.

## Commands

Every `--output` path must be new; failures and old reports are retained.

```bash
python -m gopyt.project_context test examples/orders --output /tmp/orders-tests.json
python -m gopyt.obligations report examples/orders \
    --test-receipt /tmp/orders-tests.json \
    --acceptance benchmarks/order_lifecycle/evidence/development-04/report.json \
    --output /tmp/orders-obligations.json
# ... edit sources ...
python -m gopyt.obligations impact examples/orders --before /tmp/orders-obligations.json \
    --test-receipt /tmp/orders-tests.json --output /tmp/orders-impact.json
python -m gopyt.obligations facts examples/orders --output /tmp/orders-facts.json
```

`report` compiles a copied source graph, computes facts, loads the registry and
binds evidence. Exit 1 means a registry error, a compile failure, or an obligation
naming a function that does not exist in the compiled program. Exit 0 does not
mean every obligation is verified: read `gaps` and `totals.with_gaps`.

`impact` answers the questions an agent needs before and after a change:

- **What APIs exist?** `apis`: public declarations of every affected module,
  from checked parser nodes, plus `after.current.public_declarations` for all.
- **Which requirements apply?** `obligations_applying`: obligations whose
  functions are in the affected set or whose state fields changed source.
  Proposed and unresolved obligations appear too, so a change that touches a
  function subject to a proposed policy is warned about rather than silently
  implementing it.
- **What else could be affected?** `affected_functions`: reverse call closure of
  changed or re-contracted functions plus readers of record fields written by
  changed or affected functions (`affected_via_field_coupling`), iterated to a
  fixed point. A changed function conservatively changes every field it writes,
  even when the syntactic source category is unchanged, because `computed` vs
  `computed` cannot show that the arithmetic is the same. This uses compiled
  call edges and syntactic field writes; it is conservative over what the
  compiler resolved and does not model effects or external state.
- **Which checks ran against the current inputs?** `after.evidence` and
  `stale_evidence`: every supplied receipt or acceptance report with its status.
- **What remains unknown?** `unknowns`: effectful functions in the affected set,
  framework HTTP dispatch, generic declarations that may have uninstantiated
  bodies, and the note that nothing is stale when no file changed. That note is
  suppressed — and further unknowns recorded — when the comparability premises
  differ between the reports: a different engine build
  (`engine_changed: true`; body/contract digest equality across engine builds
  is not established), a before report produced for a different package root
  (change lineage not established), or a changed registry
  (`registry_changed: true`; obligation definitions were added, removed or
  re-scoped, so the after report alone does not describe what was there
  before).

## Staleness

Evidence is bound by content hashes, never by timestamps or file names:

- A test receipt is `stale` when its source identity (all manifest/lock/spec/impl/
  test bytes of the whole dependency graph) or its engine identity (every
  non-test Python file under `gopyt/`, including this tool, and the Python
  version) differs from the current tree. A stale receipt contributes no test
  outcomes and no runtime-contract execution evidence; its tests show as
  `stale_receipt`. Two **current** receipts that disagree about the same test
  are a contradiction, not something argument order decides: the test's
  outcome becomes `contradictory` everywhere it is cited, the gap names it,
  and `evidence.contradictory_tests` lists it.
- An acceptance report is `stale` when any recorded hash of a package file, a
  compiler file or another suite dependency differs; the differing files are
  listed. It is `partial` when its inventory does not cover every current
  package and compiler file, `contradictory` when `passed: true` disagrees with
  its own scenario counts or failure list, and `unbound` when it carries no
  versioned evidence schema, no suite identity, or no inventory the package can
  be identified from. Only a complete, consistent, fully matching report can be
  `passed`, and acceptance representations are bound to reports by suite
  identity, never by file basename. A passing binding discloses its corpus size
  in the summary ("bound to current inputs (N scenarios)"), and a suite
  identity that binds more than one distinct report produces a gap: the
  identity string alone does not identify a corpus, and N is not a quality
  claim. `source_before` keys are repository-root
  relative paths; duplicate JSON keys make a report `invalid` rather than
  last-wins.
- The prose contract is `stale` when the document's hash differs from the one
  recorded in the registry: every obligation is then marked
  `applicability: stale` with a leading gap, and the report's `stale_evidence`
  names the document. A registry that cites no document, or cites a missing
  one, marks every `contract`-authority obligation `applicability: unbound`
  with a leading gap. Test and acceptance facts about execution are preserved
  as facts; what is stale is the claim that they still satisfy the requirement.

Because the engine identity covers all of `gopyt/*.py`, editing any compiler or
tooling file invalidates all existing receipts. Produce final evidence after the
last code edit. This is deliberate: a receipt from a different tool build is not
evidence about this one.

## Limits

- Contracts are boolean GoPyT over arguments and `result`. They cannot see
  history: “refund shipping exactly once” is expressible only because the state
  records cumulative quantities; a rule about an event that leaves no trace in
  state needs a state field first.
- Obligations that describe ordering among several failure outcomes (precedence)
  are partially expressible; the rest stays with tests and the acceptance corpus.
- `preserved_field` is syntactic. `fee: state.fee` holds; `fee: keep(state)` is
  `not_established` even when `keep` returns the field unchanged.
- Test reach is a call-closure fact, reported as `reached_by_passing_tests`
  with an explicit `reach_basis`: the tool has no runtime call instrumentation,
  so a test that reaches a function statically may never have executed a call
  (a call guarded by a branch it does not take still counts as reaching). Use
  acceptance corpora or targeted tests for branch coverage.
- Hashes are integrity checks, not signatures. Anyone who can rewrite a receipt
  can recompute its digest; replay with `project_context verify` re-executes.
- A runtime contract that is statically reached by passing tests may never have
  executed a call at all, let alone taken the violating branch; the report says
  so, and only the acceptance corpus or a targeted test establishes execution.
- `impact` is conservative: on the ten-module engine a one-line change to a
  state-writing function lists most obligations. Precision is low by design;
  the value is in not omitting.
- Independent challenge results, including the defects only the external oracle
  caught (an invented `courtesy` operation and a widened legal table), are in
  `benchmarks/obligations/challenge/`. That run also found two false alarms
  (a local alias reported as a violation; an equivalently parenthesised clause
  reported missing), one silent pass (a misspelled field name in
  `preserved_field`), and all-or-nothing registry loading. All four were fixed
  afterwards with regressions in `gopyt/test_obligations.py`; the retained
  challenge results describe the tool before those fixes.
- The 2026-09-06 obligations-phase review
  (`docs/reviews/claude-obligations-2026-09-06/`) reproduced four more defects —
  incomplete/contradictory acceptance could read as passing, static reach was
  labelled as execution, arithmetic changes dropped downstream field readers,
  and a stale contract document did not mark obligation applicability. All four
  are fixed with regressions in `ReviewRegressions`; the review and its probe
  results are retained and describe the tool before those fixes.
