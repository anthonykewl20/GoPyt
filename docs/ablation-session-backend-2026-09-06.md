# Ablation measured-session backend — decision memo (2026-09-06)

Advisory to whoever freezes and runs the obligation-ablation campaign (session
B owns the freeze). Written by session A after investigating the measured-model
requirement: "Use GLM as a measured model only through genuinely fresh sessions.
Record the exact available model identifier and configuration. Do not invent
model calls or claim another model was used when it was not available."

## Facts (checked 2026-09-06 evening)

- The runner's session backend (`benchmarks/agent_reliability/session.py`) is
  hard-wired to the Codex CLI; `runner.py:freeze` records model
  `'gpt-6-astra'` in the manifest. ~/.codex/config.toml still selects
  gpt-6-astra and auth.json exists (used by the 2026-09-06 reliability
  benchmark's 24 sessions). No GLM provider is configured in codex.
- This harness runs GLM (`glm-5.3`, maximum thinking effort) through the zcode
  Agent facility with OAuth (ZCODE_BASE_URL https://zcode.z.ai). There is no
  OpenAI-compatible API key in the environment to point codex at, and
  extracting the harness's OAuth credential into codex would be neither
  authorized nor honest plumbing.
- The Agent facility spawns genuinely fresh sessions: no inherited
  conversation context, full tool access, dense final report. Verified this
  session by an independent-review subagent (read-only by instruction).

## Options

**A. Codex CLI with gpt-6-astra (current runner).**
+ Full JSON event stream → observed-tool-item counts, command lists,
  "context retrieved" per retained command events, hard wall/tool budget
  kills, Bubblewrap filesystem allowlist exactly as the frozen METHODOLOGY
  describes.
− The measured model is not GLM, contradicting the campaign instruction to
  measure GLM; the manifest would truthfully record gpt-6-astra. If the
  instruction's intent is "measure GLM", this option measures the wrong thing.

**B. GLM (glm-5.3) through genuinely fresh harness subagent sessions.**
+ Matches the instruction; model identifier and configuration recordable
  exactly (`glm-5.3`, thoughtLevel max, fresh session per trial, no inherited
  context, no access to oracles/prior trials by construction of the prompt).
+ A one-line verified property: each trial is a separate agent with no shared
  conversation; the authoring session never forwards oracle or prior-trial
  content.
− Weaker instrumentation than codex: no event stream. Budgets (600 s / 80
  items) can be instructed but not enforced; wall time is measured by the
  controller; "context retrieved" can be measured as a lower bound with a
  filesystem access monitor (inotify) on the trial work tree, or not at all.
− Weaker isolation: a harness subagent shares the host filesystem; the
  Bubblewrap allowlist paragraph of the frozen METHODOLOGY would not hold.
  Mitigations: prompt-only restrictions (identical to the codex prompt's
  "Only use /work..." clause), post-hoc review of the final message and
  produced diffs for evaluator/oracle references, and disclosure.

## Recommendation

Option B, with a METHODOLOGY amendment written **before freeze** (nothing is
frozen yet, so this is a pre-freeze correction, not a campaign restart):

1. Replace the model/backend block in the manifest: model `glm-5.3`,
   backend `zcode-agent fresh session`, thoughtLevel `max`, engine harness
   version, per-trial session identity; never record gpt-6-astra for trials
   it did not run.
2. Replace the isolation paragraph: filesystem boundary becomes
   "instruction-level restriction plus post-hoc contamination review; no
   Bubblewrap"; state that command events and hard budget enforcement are
   unavailable, wall time is controller-measured, and budget compliance is a
   self-reported/instructed limit recorded per trial.
3. Keep everything else (arms, tasks, oracle, gate, rubric, retention rules)
   unchanged; the measurement comparisons are within-campaign (all four arms
   on the same backend), so arm comparability survives the backend change.
4. Run an explicit contamination probe as part of the review: grep every
   trial's final message and diffs for evaluator/oracle path references
   (benchmarks/orders_multi, evaluator, oracle), and retain the probe results
   with the campaign.

If instead option A is chosen, the report must say plainly that the measured
model was gpt-6-astra through codex, not GLM, and why.

## Implementation sketch for B (for session B to own/merge)

- A `session_glm.py` sibling to `session.py` with the same return shape
  (exit_code None/0, terminal_event 'agent.completed', censored False,
  wall_seconds measured, observed_tool_items None + tool_count_definition
  noting unavailability, usage None + note). The trial driver invokes the
  harness Agent facility with the frozen prompt verbatim (only the work path
  differing), then snapshots the work tree exactly as the codex path does.
- Since the controller cannot be a separate process here, the practical route
  is: the controller prepares the trial tree, then dispatches one fresh
  subagent per trial sequentially or in small batches, then evaluates each
  tree with the existing bwrap-evaluated oracle path (evaluation isolation
  unchanged — Bubblewrap still guards the EVALUATION commands).
- Retention: every dispatched trial is retained whatever the outcome;
  launch failures (subagent error) are recorded as dispositions, never
  retried silently.

## Addendum (later the same evening, after v2 froze)

Campaign v2 is now frozen with `session.py` (codex backend) and a manifest
recording gpt-6-astra; `verify_freeze` pins both, so the backend decision is
not an in-campaign switch. The actual choice is:

- **Run v2 as frozen on codex/gpt-6-astra.** The manifest is truthful, event
  instrumentation and Bubblewrap isolation are exactly the frozen
  METHODOLOGY's, and the report must state plainly that the measured model was
  gpt-6-astra through the Codex CLI (not GLM). This measures the ablation
  question (does obligation tooling change what agents get right) on an
  already-validated harness.
- **Freeze a v3 campaign with the GLM amendment** (METHODOLOGY isolation and
  budget paragraphs amended pre-freeze, a fresh-session backend recorded in
  the manifest, evaluation still under Bubblewrap). Required if the campaign
  must specifically measure GLM; costs one more freeze + baseline + review
  cycle and retains v2 as unexecuted.

Either is honest; what is not acceptable is a manifest that names a model that
did not run the trials. (The approval gate recorded the same condition.)

## Decision (recorded 2026-09-07 by session A, before launch)

**Option A: run campaign v2 as frozen on the Codex CLI with gpt-6-astra.**

Reasons, in order:
1. v2 is frozen with a passing real baseline gate (all four arms) and two
   independent approving review gates (session A's, bound to the manifest
   sha256, and a second fresh-context reviewer). Option B would discard a
   validated freeze and repeat the full freeze→baseline→review cycle.
2. The Codex backend is the only one carrying the frozen instrumentation:
   Bubblewrap allowlist+PID isolation, per-session event capture (the
   "context retrieved" and "observed tool items" outcomes), and hard
   wall/tool-budget enforcement. The GLM fresh-session alternative cannot
   enforce budgets or capture command events without a METHODOLOGY amendment,
   i.e. a weaker campaign.
3. The manifest truthfully records model gpt-6-astra, reasoning high, and the
   trials will actually run exactly that. Reports will state plainly that the
   measured model was gpt-6-astra through the Codex CLI — not GLM. GLM was
   not used for any measured session and no report will claim it was.
4. The authenticated Codex CLI is the same resource the 2026-09-06
   reliability benchmark used (24 sessions); no new resource is acquired.

Session B's note in PARALLEL.md explicitly deferred this decision; it is made
and recorded here before any launch.
