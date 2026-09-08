# GoPyT independent reliability campaign — consolidated report

Compiled 2026-09-08 by session A, day seven consolidation unit. Every
number below was recounted from raw artifacts in this final pass; three
earlier summary errors were caught and corrected by dated entries in
prior integration audits (2026-09-08 ×3), and one here (v12c's
directories are retained infrastructure failures with codex sessions
that aborted before any trial content — excluded from measured totals,
bringing the final count to 234, superseding the "242 raw dirs" figure
that includes them).

## Measured base (final, tree-verified)

- **234 measured agent trials = 164 gpt-6-astra (reasoning high, Codex
  CLI) + 70 GLM (glm-5.3, fresh zcoder:glm-main subagent sessions).**
- 13 executed arm campaigns (v2, v3, v4, v6, v8, v9, v10, v11, v12e,
  v13, v14, v15, v16), 1 adoption probe (v17, 6 identical-cell GLM
  sessions), 1 deterministic static-enforcement benchmark (24 frozen
  single-fault edits; GoPyT 15/24 vs strict mypy 12/24), 7 context
  pilots/growth measurements.
- 7 rejected preparations/freezes retained with their gates and
  REJECTED.md (v1, v5, v7, v12/v12b/v12d, v15's first build, plus
  v12c's 8 no-session dispositions); 3 independent integration audits
  with dated corrections; every campaign individually reviewed by fresh
  subagent reviewers (rubric-based; same-family judgment, disclosed).

## Demonstrated results (vs hypotheses — the distinction is the report)

1. **DEMONSTRATED: on gpt-6-astra, no arm advantage anywhere.** Nine
   campaigns (v2, v3, v4, v6, v8, v10, v12e + the v13/v14 GLM arc's
   astra control) tied on every scored dimension across strong/weak
   guidance, 15/33 modules, 2/6 seeded defects, bundled policy, missing
   contract prose, and reduced reasoning effort. The obligation tooling
   cost tokens everywhere and never changed an outcome.
2. **DEMONSTRATED: correctness is model-dependent.** GLM (v9) failed
   task B 0/8 where astra was 48/48 on the same frozen cells; the
   failure is misreading a stated rule over code that contradicts it,
   reported honestly (0 contradicted claims in v9; the program's few
   contradicted claims came later, under pressure, and were caught by
   review).
3. **DEMONSTRATED: the tooling arc.** Registry prose does not catch the
   misread (v9); an executable answer key catches it exactly when used
   (v11: users 5/5 on S1, rejecters 0/3); a registry pointer flips GLM
   adoption 0/2→2/2 (v13); a descriptive sentence is read and followed
   by nobody (v15: tooled S2 0/2); an executable one-command probe is
   decisive when run (v16: 18/189→0, reviewer-reproduced) but adoption
   is 1/2 in campaign and **0/6 on identical replicas (v17)**. On
   astra the same pointer changes nothing (v14: oracle 0/8, 8/8
   correct without it).
4. **DEMONSTRATED: GoPyT's compiler-level guarantees do separate from
   the comparator's** in the one deterministic benchmark (exhaustiveness,
   spec/impl drift, cross-enum discipline) — the language's designed
   dimension — while neither checker catches soundly-typed business
   faults.
5. **DEMONSTRATED: the evidence binder is materially more honest** after
   this campaign's seven counterexample classes (contradictory receipts,
   suite-identity collisions, engine/lineage/registry substitutions,
   dead exemptions, uninstantiated generics, foreign baselines) — each
   repaired with fail-first regressions and independent review.
6. **STILL HYPOTHESIS (bounded by measurement, not proven):**
   bounded-context at repository scale. Tool-side selection improves
   with growth (0.93→0.58), agent-side retrieval never narrowed in any
   condition; RP-001 remains unproven.

## The goal's six questions — final answers

1. **What became more reliable?** The verification tooling itself:
   seven classes of misleading evidence now surface instead of
   silently passing; the review-gate process caught three bad freezes
   pre-session and one contaminated preparation mid-campaign; three
   integration audits caught every arithmetic drift in our own
   summaries.
2. **What still produces misleading results?** Fabricated-but-internally-
   consistent evidence (hashes are not signatures); value-flow and
   contract-only context omissions; and — the arc's core — any
   verification an agent declines to run.
3. **Did tooling improve measured agent outcomes?** **No**, on either
   model, in any of the eleven conditions measured. The single positive
   mechanism (oracle-when-used) is gated by adoption, which no artifact
   in the series raised reliably.
4. **Did GoPyT outperform equivalent Python assistance?** **No** at the
   agent level (the comparator was the only arm to fix a v9 task-B
   defect; adoption scattered across arms). **Yes** in the one
   deterministic compile-time benchmark — disclosed as a different
   dimension, not an agent result.
5. **Is bounded local reasoning demonstrated?** Tool-side: partial and
   improving with scale. Agent-side: no — tooled agents always read at
   least as much.
6. **What should the next phase address?** Adoption under
   self-direction (the binding constraint, measured 0/6 for the best
   tool); multi-model replication when a second usable backend exists;
   task families without a written contract (session B's v12e direction
   showed honesty separating where correctness could not).

## What this report does not claim

No production readiness; no cross-model generality beyond two backends;
no external human adjudication (all reviews same-family, disclosed);
single-subject business domains; and the statistical unit throughout is
the session, not the scenario. Every campaign, rejection, audit and
correction is retained under docs/agent-evidence/ and docs/reviews/.

### Addendum (2026-09-08, later — v18 answers the report's top next-phase question)

The report's question 6 named "adoption under self-direction" as the
next phase's priority. Probe v18 answered it the same day: on the
identical tooled cell where self-directed generator adoption was 0/6
(v17), a single user-level directive line produced 6/6 execution
(adapter mode, reviewer-reproduced before/after numbers) and 6/6 S2
fixes (3/6 full passes; claims 43/0/0/2; integrity 6/6). The
constraint is instruction provenance, not tool capability or model
skill. Final program totals: 240 measured trials (164 gpt + 76 GLM).

### Addendum 2 (2026-09-08, later still — v19 replicates the provenance effect with error bars)

Probe v19 added 12 identical-cell GLM sessions (6 per condition; pooling
rule fixed in advance). Pooled with v17/v18: generator execution
self-directed 3/12 vs directed 12/12 (Fisher exact p ≈ 0.0005); S2
fixed 3/12 vs 12/12 — and perfectly coupled to execution (9/9 executors
fixed it, 9/9 skippers did not). v17's 0/6 point estimate did not
replicate (v19 self: 3/6, and those three executors were the arm's only
full passes): the honest statement is that self-directed adoption is
rare and high-variance, not never. The v18 addendum's "6/6 vs 0/6"
formulation is superseded by these error-barred pooled figures. Final
program totals: **252 measured agent trials = 164 gpt-6-astra + 88 GLM**
(13 campaigns + 3 adoption probes + 1 deterministic benchmark; 7 retained
rejections; 3 integration audits). Two further contradicted claims were
caught by review (randomized-equality claims in evaluator-failed trials)
— the review layer continues to earn its keep.

### Addendum 3 (2026-09-08, final — v20 completes the provenance × model matrix)

The user directive was replicated on gpt-6-astra (v20: 8 codex trials,
boundary_probes in every arm, directive in every prompt; gate-reviewed
with the insertion verified byte-exact). Result: 8/8 correct (ceiling)
and directive-following 8/8 full — every session executed the generator
with --adapter-cmd and recorded the summary. The matrix is complete:
adoption follows instruction provenance on BOTH models (GLM 3/12 self
vs 12/12 directed; astra 0/8 self vs 8/8 directed), while outcome
impact appears only where the model's own verification fails (GLM task
B: S2 coupled 9/9 to execution) and never where it already succeeds
(astra: process changed, outcomes unchanged). Final totals: **260
measured agent trials = 172 gpt-6-astra + 88 GLM**.

### Addendum 4 (2026-09-08, final — v21 bounds the headline estimate; v20 numbers consolidated)

v20 (directive on astra): 8/8 correct, directive-following 8/8 full —
matrix complete (see Addendum 3's table plus astra-directed 8/8).
v21 (12 more self-directed GLM replicas): pooled 24-sample estimate —
generator execution 7/24 (29%, exact 95% CI [14%, 50%]), S2 fixed
6/24, executor→fix 9/12 vs 0/12 non-executors, with one session
running the probes, diagnosing the defect correctly, and shipping
unfixed anyway. Directed adoption remains 18/18 across models. Final
totals: **272 measured agent trials = 172 gpt-6-astra + 100 GLM**. The
program's closing claim, now with proper intervals: verification
tooling changes outcomes exactly when it is executed; execution is
universal under a user directive on both models and roughly one-in-three
under self-direction — and even execution occasionally stops short of
the fix.

### Addendum 5 — static-enforcement successor evidence (2026-09-08)

The original 24-fault 15–12 static comparison above is superseded by session
B's retained v14c 33-fault comparison: **GoPyT 25/33, strict mypy 18/33**.
The margin is contract/interface erosion (8–2) plus enum confusion (3–2);
exhaustiveness is tied at 6–6. This supersedes the earlier attribution of part
of the measured gap to exhaustiveness. These deterministic runs add no agent
sessions to the program total.

A subsequent local audit found T1's Python replacement had a missing closing
parenthesis; its retained rejection was a syntax error. A separately retained
v14d-t1 successor repairs that one character and confirms the same 25–18
result, with the other 32 fault records identical and both clean baselines
passing five repeated runs. The corrected T1 is rejected for the intended
str-for-int error. v14d-t1 is locally verified, not independently reviewed.

See [the successor report](agent-evidence/2026-09-08-static-enforcement-v14d-t1/ENFORCEMENT-REPORT.md)
and [the audit](reviews/2026-09-08-v14c-t1-followup/REVIEW.md). The result supports
specific compiler guarantees on this corpus; it does not establish a general
75.8% detection rate or an agent advantage. The five clean repetitions do not
constitute five independently sampled applications.
