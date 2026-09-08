# Fresh-session agent effectiveness: quotation pilot

**GoPyT completed this small-project pilot successfully. Typed Python and strict
TypeScript completed the same tasks successfully. These results demonstrate
feasibility; they do not demonstrate a GoPyT accuracy advantage.**

All 18 scheduled fresh sessions finished within their budgets. Each language
passed four implementation trials and two clarification trials. Every final
submission passed its strict checks, public tests, applicable source-integrity
review, and all 1,202 independently authored business cases. All six clarification artifacts
also passed the separately recorded model-review rubric. There were no replaced,
discarded, timed-out, or infrastructure-failed measured attempts.

| Language | A: pricing change | B: cross-module contract change | C: clarify missing policy |
| --- | ---: | ---: | ---: |
| GoPyT | 2/2 | 2/2 | 2/2 |
| Typed Python | 2/2 | 2/2 | 2/2 |
| Strict TypeScript | 2/2 | 2/2 | 2/2 |

Passing implementation acceptance does not mean every agent statement was
verified. Review found two TypeScript sessions with optional-test claims that
the retained output did not independently establish. Those observations are
preserved below, separately from controller-verified implementation correctness.

## What was measured

The [frozen protocol](agent-evidence/2026-09-05-pilot-v1/methodology.md) and
[business contract](agent-evidence/2026-09-05-pilot-v1/contracts.md) preceded the
measured sessions. Each trial began from a fresh copy of the same language's
unchanged quotation application. Tasks were independent, not cumulative:

- **A:** introduce a 10% member discount at an undiscounted subtotal of 20,000
  cents, keeping 5% below that boundary and preserving other behavior.
- **B:** introduce the typed `nonprofit` customer with a 7% discount and the
  `invalid_quantity` outcome, updating producers and exhaustive consumers while
  preserving quantity-validation precedence and other rules.
- **C:** respond to “Give loyal customers better shipping.” No eligibility,
  benefit, or interaction policy was supplied. The common prompt for every task
  instructed agents to request missing business policy through a clarification
  artifact. C's evaluation rubric was withheld from the prompt.

The common JSON-lines interface uses integer cents and an explicitly bounded
input domain. GoPyT's fixed adapter calls the actual compiled bytecode on its VM;
it contains no quotation calculations. Python and TypeScript have equivalent
fixed adapters and strict static checking. All three have corresponding model,
pricing, quote, and checkout modules and exhaustive customer/outcome consumers.

| Baseline | Business-source lines | Business-source bytes | Accessible guide bytes |
| --- | ---: | ---: | ---: |
| GoPyT | 143 across eight spec/implementation files | 3,051 | 6,131 |
| Python | 103 across four files | 2,877 | 2,674 |
| TypeScript | 83 across four files | 3,084 | 2,834 |

Lines include blanks and comments. This is a very small unfamiliar project,
approximately 3 KB of business source per language. The GoPyT guide includes
syntax and canonical-tool instructions. Its larger guide and additional spec
files are part of the measured language/tooling package, not controlled away.

The fixed configuration was `gpt-6-astra`, high reasoning, Codex CLI 0.153.4,
fresh ephemeral sessions, a 360-second deadline and a limit of 40 observed tool
items. Three sessions ran concurrently per block, one per language; the six-block
schedule was seeded and frozen. Tool limits are detected from streamed events,
so a triggering operation can already be in flight. No measured trial reached
either limit. There was no hard token cap.

The compiler/runtime environment was GoPyT 0.1.000 with bytecode version 2,
Python 3.14.6, mypy 2.3.0, TypeScript 5.9.3, Node 24.18.0, and Node declarations
24.13.3. Strict configurations and dependency metadata are retained in the
[campaign manifest](agent-evidence/2026-09-05-pilot-v1/manifest.json). Two excluded
CLI smoke sessions tested file creation, authentication and instrumentation;
neither attempted a quotation task. Their logs and other setup probes are in
[setup evidence](agent-evidence/2026-09-05-setup/README.md).

## Correctness and resource observations

Each A evaluation covered 271 changed-rule cases and 931 regressions. Each B
evaluation covered 464 changed-rule cases and 738 regressions. C preserved all
1,202 baseline cases. Across measured submissions this is **21,636/21,636 business
responses**, plus 3,606/3,606 baseline-validation responses before measured runs.
These case counts describe coverage. The experimental unit is an agent trial:
there are 18 trials, not 21,636 independent observations of agent effectiveness.

Every cell below passed all applicable implementation or clarification gates.
The times and tool counts preserve both repetitions individually; no failed
attempts are omitted.

| Language | Task | Repetition 1: agent time / tool items | Repetition 2: agent time / tool items |
| --- | --- | ---: | ---: |
| GoPyT | A | 78.2 s / 9 | 94.2 s / 12 |
| GoPyT | B | 95.8 s / 12 | 102.6 s / 12 |
| GoPyT | C | 40.7 s / 6 | 46.5 s / 8 |
| Python | A | 64.9 s / 6 | 61.8 s / 7 |
| Python | B | 75.5 s / 7 | 69.6 s / 8 |
| Python | C | 38.7 s / 5 | 42.0 s / 5 |
| TypeScript | A | 90.7 s / 8 | 94.9 s / 8 |
| TypeScript | B | 99.1 s / 8 | 108.0 s / 8 |
| TypeScript | C | 49.4 s / 6 | 42.8 s / 4 |

For the four implementation trials per language, descriptive resource medians
were:

| Language | Agent wall time, median (range) | Median tool items | Median reported input tokens | Median reported output tokens |
| --- | ---: | ---: | ---: | ---: |
| GoPyT | 95.0 s (78.2–102.6) | 12 | 120,063.5 | 2,158.5 |
| Python | 67.3 s (61.8–75.5) | 7 | 84,154 | 1,439.5 |
| TypeScript | 97.0 s (90.7–108.0) | 8 | 104,109.5 | 2,333.5 |

CLI-reported input tokens include repeated and cached context across calls;
they are not unique project tokens or a monetary cost. Raw cached-input and
per-trial usage are in [the summary](agent-evidence/2026-09-05-pilot-v1/summary.json)
and [trial CSV](agent-evidence/2026-09-05-pilot-v1/trials.csv). Agent wall time
includes service latency, reading, editing and local checks. Concurrent sessions,
unequal documentation, tool choices and sandbox behavior limit causal comparison.
These are **agent workflow timings, not application execution benchmarks**.
Python's lower observed times here do not establish a general language ranking.

## What review found

[Implementation review](agent-evidence/2026-09-05-pilot-v1/reviews/implementation.json)
found no source-level type erasure, suppression of strict checks, invented
business policy, invented domain API, or observed prohibited access in the 12
implementation trials. GoPyT's four post-edit check failures were expected
`lock_stale` diagnostics, repaired using generated lock contents. No semantic or
type-error repair was needed in those GoPyT submissions. One harmless Git-diff
attempt failed because this workspace is not a Git repository.

Some TypeScript optional subprocess probes encountered sandbox `EPERM` errors;
the documented compiler and public-test commands succeeded. Two final claims
were not independently supported by the retained optional-probe output:

- `a-typescript-r1` claimed an adapter boundary check succeeded, but that reply
  was absent from the retained compound-command output. Its 19 direct checkout
  checks had a confirming marker.
- `b-typescript-r1` claimed 23 targeted checks passed. The original subprocess
  probe failed; the fallback retained two adapter replies but no marker confirming
  the 23 checks.

This does not prove those checks never executed: tool-output completeness remains
uncertain. The controller independently verified both final implementations.
The observation supports requiring test evidence rather than accepting an
agent's test-count claim. It is not a defensible estimate of a TypeScript-specific
hallucination rate, and it does not retroactively change the frozen acceptance
criteria.

[Clarification review](agent-evidence/2026-09-05-pilot-v1/reviews/clarification.json)
found that all six C submissions asked about loyalty eligibility, exact shipping
benefits, and interaction with zones and existing post-discount/free-shipping
rules. All left business sources unchanged. The shared clarification protocol
worked in all three languages; the pilot cannot attribute that behavior to GoPyT.

Reviews were separately tasked model assessments from the same model family,
not external human adjudication. The implementation reviewer had authored the
baseline fixtures; the oracle/clarification reviewer did not inspect subject
implementations when authoring the oracle. These independence limits are recorded
in the review artifacts.

## Validation and evidence limits

Before measured execution, six temporary-copy probes confirmed that adding an
unhandled customer or outcome variant fails the checker in every language.
The independent oracle passed ten hand-calculated sentinels, twelve protocol
checks and five deliberately wrong-policy probes. Runner probes exercised event
accounting, deadline/process-descendant handling and retained infrastructure
failures. Seven evidence-checker tests include rejection of inconsistent success
claims and acceptance of honestly retained failures.

The final independent evidence check reconciled all 18 scheduled dispositions,
unique session IDs, frozen files, source inventories/diffs, raw event token/tool
counts, process reports, review hashes and summary statistics: **no discrepancies
or pending reviews**. The checker validates record consistency, not model honesty
or semantic truth. Recorded wall times have no independent raw timestamp anchors.

An additional [excluded evaluation replay](agent-evidence/2026-09-05-pilot-v1/replay/report.json)
rebuilt clean copies of all 18 submissions. Every static gate and all 1,202 cases
per submission passed again, with exactly matching output and corpus hashes and
matching toolchain version outputs. Original submissions remained unchanged.
These were evaluation replays, not additional agent trials or repaired attempts.

Evaluator withholding was cooperative. Fresh sessions received no evaluator or
previous trial feedback, and reviewed traces showed no forbidden access, but an
adversarial OS read boundary was not established. Installed toolchain contents
were recorded by versions and dependency locks rather than a complete installed
file hash. Account-level behavior, provider caching and pretrained language
familiarity were not eliminated. Two repetitions per cell, one model and a tiny
project cannot support statistical superiority, massive-repository claims or
claims about every AI agent. Rust and Go were not arms of this pilot.

This work added benchmark fixtures, independent evaluation, fresh-session
instrumentation, evidence checking and documentation. It did not change the
GoPyT runtime or release version. The prior 477-test runtime regression results
remain historical evidence; that suite was not rerun for these benchmark-only
changes.

## Implication for GoPyT's main goal

A fresh agent could learn enough GoPyT from its local guide to perform these
changes correctly. That is useful evidence of small-project learnability. It
does not establish the proposed “native language” familiarity across AI models,
and it does not show that language syntax prevents misunderstood business intent.
Typed Python and strict TypeScript matched the same acceptance results.

The next experiment should increase difficulty before increasing the count of
these already-solved tasks: a realistic order/inventory/refund application with
state changes, interacting policies, stale documentation, missing requirements
and changes across more modules. Then test fresh-session maintenance at larger
repository sizes, with multiple models, repeated trials, and an explicitly
isolated evaluator. Larger line counts alone are insufficient; relevant facts
must be distributed across realistic dependencies.

For product development, prioritize compiler-derived project context and
verifiable check results: an agent should retrieve exact public interfaces,
effects, dependencies and contract changes, and attach test results to the source
hash actually tested. Compare those tools against equivalent project-context
support in the other languages, including an ablation without the tools. That
would test GoPyT's intended advantage more directly than adding unrelated syntax
or declaring victory from this pilot.

The runnable entry point is [the benchmark guide](../benchmarks/agent_eval/README.md).
Recompute the retained summary and check its consistency from the repository root:

```bash
python tools/summarize_agent_pilot.py docs/agent-evidence/2026-09-05-pilot-v1
python tools/check_agent_evidence.py docs/agent-evidence/2026-09-05-pilot-v1
sha256sum -c docs/agent-evidence/2026-09-05-pilot-v1/SHA256SUMS
```
