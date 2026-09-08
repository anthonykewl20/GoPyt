# Explicit business obligations, stale evidence and change context — 6 September 2026

**No measured fresh-agent sessions were run in this phase.** This report covers a
tooling extension, its independent challenge, a larger validated subject with an
independent oracle, and a drafted (not frozen) ablation protocol. It shows no
advantage of GoPyT over Python, TypeScript or Go, and it does not settle RP-001.

## What changed and why

The reliability experiment ended with a counterexample: `gopyt check` accepts a
well-typed implementation that violates a prose business rule, and a green test
run says nothing about which rules the tests reached. This phase adds a bounded
capability on top of the existing compiler context and receipts:

- `gopyt/obligations.py` (`python -m gopyt.obligations report|impact|facts`),
  documented in [obligations.md](obligations.md). A registry gives each business
  requirement a stable id and an authority (`contract`, `proposed`, `unresolved`).
  Representations are executable postconditions (`ensures`/`requires` clauses
  that must exist verbatim on the checked spec), static field-preservation checks
  over every compiled constructor, named tests whose call closure must reach the
  obligated functions, and external acceptance reports bound by file hashes.
  Every obligation is reported per scope with explicit gaps; nothing is labelled
  a proof. `impact` computes, from compiler-resolved call edges plus record-field
  coupling, which functions, declarations, obligations and evidence a change
  touches, and lists unknowns (effects, HTTP dispatch, generics).
- Staleness is by content hash: a test receipt, an acceptance report or the
  prose contract becomes `stale` when its recorded sources, compiler files or
  document differ from the current tree. Editing any file under `gopyt/` stales
  all receipts; final evidence below was produced after the last code edit.
- `examples/orders` gained 18 executable contract obligations (`ORD-*`) with
  `ensures` clauses on `transition`, `refund`, `pay`, `cancel`, `quote` and
  `place`, six more tests, and a registry that keeps the reliability benchmark's
  task-B return policy as **proposed** (`POL-B-*`, prose only, not in the
  baseline) and courtesy returns as **unresolved** (`POL-C-001`). Behavior did
  not change: independent acceptance passed 3,908/3,908 scenarios and 28/28
  transport probes on the final sources (`development-05`; `development-03` is
  a retained failed attempt caused by editing during the run).
- A compiler defect found by the challenge was fixed in `check.py`: a spec
  `use` satisfied only by a contract was reported unused (E014) when an earlier
  body in the impl file had already used the name. Regression in
  `gopyt/test_obligations.py`.

## Independent challenge (Priority 3)

`benchmarks/obligations/challenge/` was authored without reading implementation
bodies: an own oracle with 18 literal fixtures, 12 compiling mutants plus
retained non-compiling attempts, and 7 misleading artifacts. Against the
`examples/orders` package as it stood before the fixes below:

| layer | mutants detected (of 12) |
|---|---|
| package tests | 6 |
| runtime `ensures` trap (tests or oracle run) | 8 |
| static `preserved_field` | 1 |
| obligation-report gap beyond acceptance staleness | 7 |
| challenge oracle | 12 |
| caught by nothing except the external oracle | 2: an invented `courtesy` operation (M09b) and a widened legal table letting `refund` run from `paid` (M12b) |

All seven artifacts were handled as intended: stale receipt flagged with the
reason, mock-only test flagged as not reaching an obligated function, prose-only
and unresolved rows reported as unverified, a clause absent from the spec
reported `missing_clause`. The challenge also found two false alarms (a local
alias reported as a violation; an equivalently parenthesised clause reported
missing), one silent pass (a misspelled field in `preserved_field`), and
all-or-nothing registry loading. All four were fixed afterwards with
regressions; the retained challenge results describe the tool before the fixes.

Blind spots that remain: an invented operation or a widened transition table is
invisible to the registry unless an obligation names the table; contracts that
are reached but never take the violating branch establish nothing (the report
now says so); `impact` lists most obligations for any state-writing change in
the ten-module engine.

## Larger subject and RP-001 measurement (Priority 4, partial)

- `examples/orders_multi`: 15 modules, 338 spec / 950 impl lines, 23 tests,
  33 obligations (30 contract, 2 proposed, 1 unresolved), 66 bound `ensures`
  clauses, 7 `preserved_field` checks holding. Built from the frozen
  [multi-line contract](../benchmarks/orders_multi/CONTRACT.md) by an agent
  that read no oracle.
- `benchmarks/orders_multi`: independent oracle (19 hand-computed sentinels,
  3,186 scenarios / 30,470 commands, 82 transport probes, 9 wrong-policy
  mutations rejected, per-case rule tags, 19 unit tests). The first acceptance
  run (`evidence/development-01`) failed on two oracle-side defects: a corpus
  case outside the transport range and an `empty-input` probe the contract does
  not justify. Both were corrected; the rerun `evidence/development-02` passed
  3,186/3,186 scenarios and 81/81 transport probes, and the final obligation
  report binds it as current acceptance for all 30 contract obligations.
- Footprint (`tools/context_footprint.py`, evidence in
  `docs/agent-evidence/2026-09-06-obligations/footprint-*.json`): for a change
  to any single state-writing function, the compiler-derived closure selects
  about 10 of 21 functions and 40% of source lines in the ten-module engine,
  and about 11 of 74 functions and 17% of lines in the fifteen-module one. This
  is a measurement of what the tool selects, not proof that the selection is
  sufficient. RP-001's bounded-context claim remains unproven: two repository
  sizes from one family, and no agent trial measured whether the selected
  context was enough.
- A policy-B executable subject (`benchmarks/obligations/subjects/orders_policy_b`)
  implements the proposed return policy with `member` retained in state and
  `ensures` clauses for eligibility-from-placement, partial-return exclusion and
  shipping-refunded-once, with its own `CONTRACT-B.md`, registry (POL-B rows
  bound as contract for that subject only) and `DEVELOPMENT.md`. It passes the
  reliability benchmark's task-B oracle 1,117/1,117 and 28/28 transport probes
  (`evidence/attempt-01`; an earlier `evidence/accept-b-01` run by the
  coordinating session is retained). Its author notes two rules the clauses
  cannot express as single-transition properties: that refund never consults a
  later command's membership flag, and "shipping added exactly once"; both rest
  on cumulative recomputation, tests and the oracle. The final obligation report
  is `docs/agent-evidence/2026-09-06-obligations/obligations-orders_policy_b-final.json`
  (an earlier interim report from a coordinator-edited registry is also kept).

## Ablation benchmark (drafted, not frozen, not run)

`benchmarks/obligation_ablation/` holds a task contract, methodology, runner and
checks template for four arms (`gopyt-bare`, `gopyt-contracts`, `gopyt-tooled`,
`python-tooled`) and four tasks (audit with two seeded defects, cross-module
maintenance, missing policy, stale evidence after a contract amendment).
`prepare_subjects.py` and the baseline gate do not exist. The typed-Python
comparator (`comparators/python/`) exists with an adapter, runtime
postcondition decorators, a registry, a report script and tests, and its
retained attempt-04 passed the multi-line oracle 3,186/3,186 (attempts 01 and
03 are retained failures); its authoring subagent was terminated by the rate
limit before writing README/DEVELOPMENT, and its mypy status and equivalence to
the GoPyT arm were not reviewed. No freeze, no review gate, no sessions. The
runner has not been executed end to end.

## Validation performed and its limits

- `python -m unittest discover -s gopyt -t . -p 'test_*.py'`: 520 tests pass
  (30 new in `gopyt/test_obligations.py`), after the compiler fix.
- `benchmarks.orders_multi.test_evaluator`: 19 tests pass; challenge harness
  runs retained under `benchmarks/obligations/challenge/results/`.
- Final source-bound evidence in `docs/agent-evidence/2026-09-06-obligations/`:
  test receipts and obligation reports for `examples/orders` (18/18 contract
  obligations established across static, runtime, test and current acceptance
  scopes; only proposed/unresolved rows have gaps), `examples/orders_multi`, and
  the policy-B subject.
- Not done: any fresh-agent trial; any comparator arm; review of the
  `orders_multi` obligations registry by an independent reader; multi-model
  evidence. Same-model-family authorship of application, registry, oracle and
  challenge within one workspace limits independence to a division of labour.

## Answers to the closing questions

- Measured advantage over comparators: **none demonstrated**; nothing was measured.
- RP-001 bounded-context claim: **unproven**. The footprint numbers are tool
  measurements on two related repositories.
- Value location: the useful part of this phase is tooling (registry, hash-bound
  staleness, reach analysis, contracts as executable obligations). Equivalent
  tooling for Python is plausible and was specified but not built.

## Next steps tied to observed weaknesses

1. Bind transition tables and operation sets to obligations so invented
   operations and widened legality are detectable without an external oracle.
2. Report branch coverage of contracts, not only reach.
3. Finish `prepare_subjects.py` and the Python comparator, freeze, review-gate,
   then run the ablation with fresh sessions; keep unsupported confidence as its
   own outcome.
4. Repeat the footprint measurement on a repository from a different family and
   with agent trials that record which selected context was actually read.

## Post-review amendment (6 September 2026, later session)

This section was added after the independent review
(`docs/reviews/claude-obligations-2026-09-06/`) and reconciles the statements
above with the current state; nothing above is rewritten.

- **Correction of an internal inconsistency.** "Answers to the closing
  questions" said Python tooling "was specified but not built" while the
  ablation section said the comparator exists and passed the oracle. The
  ablation section was right: `comparators/python/` exists with adapter,
  decorators, registry, report script and tests (3,186/3,186 on its retained
  final attempt). The "not built" claim is withdrawn.
- **The four reproduced tooling defects are fixed** in `gopyt/obligations.py`
  with regressions in `gopyt/test_obligations.py` (`ReviewRegressions`):
  incomplete/contradictory acceptance evidence can no longer be labeled
  passing (versioned schema, suite identity, complete package+engine coverage
  and outcome consistency are required; suite-identity binding replaces
  basename matching), static call closure is labeled as reach with an explicit
  not-instrumented basis instead of "executed", a changed body conservatively
  changes every field it writes so downstream readers and their obligations
  stay in the impact report, and a stale contract document marks every
  obligation `applicability: stale` in `stale_evidence` and gaps while
  preserving execution facts. An independent reviewer then found one further
  P1 in the new binding code (a path-namespace mismatch that made genuinely
  complete reports read `partial` outside the unit-test layout) plus four
  hardening gaps (boolean scenario counts, decoy `gopyt` directory, unbound
  contract authority unflagged, duplicate JSON keys); all five are fixed with
  regressions. `docs/obligations.md` is updated.
- **The draft ablation gates were repaired before any measured trial**: task C
  now preserves the seeded baseline by differential output identity instead of
  inheriting task A's correct oracle; the baseline gate enforces exact
  seeded-rule-tag attribution (and window-only failures for D) and requires
  identical GoPyT adapter output hashes for approval; the review gate must
  name the frozen manifest's sha256; an ablation-specific SKU-exchange rubric
  replaces the reliability benchmark's courtesy-refund rubric; and the freeze
  inventory now protects the windowed baselines' `build/receipts`. Gate logic
  is covered by `benchmarks/obligation_ablation/test_gates.py` and a synthetic
  freeze/verify/tamper/review-gate smoke; the same four fixes are mirrored in
  the Python comparator's report script.
- **Still not done:** `prepare_subjects.py` (subject derivation for all arms
  and baselines), end-to-end baseline validation with real subjects, the
  protocol freeze, the independent review gate, and any measured session. No
  campaign exists; nothing was measured.
