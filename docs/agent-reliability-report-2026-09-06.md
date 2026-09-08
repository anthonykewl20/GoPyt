# Four-language agent reliability experiment — 6 September 2026

**This experiment does not show GoPyT beating Python, TypeScript or Go.** All four
arms tied on the measured implementation and clarification outcomes, with no
unsupported final verification claims found in review. Python had the lowest
observed median implementation workflow time and input-token use; these small,
uncontrolled resource observations do not establish a stable ranking.

Completed: 24 fresh sessions and 24 independent task reviews. The frozen protocol
is in [benchmarks/agent_reliability](../benchmarks/agent_reliability/README.md),
with raw evidence in [reliability-v2](agent-evidence/2026-09-06-reliability-v2/).

## Question and scope

The question is whether GoPyT helps an AI agent read unfamiliar code, preserve
interacting requirements, avoid invented APIs or policy, and make verification
claims supported by actual execution. Application runtime speed is outside this
experiment. In the earlier 18-session pilot, all languages tied on implementation
and clarification acceptance; Python's shorter workflow time did not establish
better business correctness.

The user's “Problem 1” maps to **RP-001: bounded local reasoning** in the
[research notebook](gopyt-native-ai-programming-languge.md). This experiment tests
cross-module reading and maintenance in a ten-module order engine. It does not
test whether context size stays bounded as repository size increases, and does
not directly induce or measure long-session forgetting. Its single-order,
single-SKU pure engine has no persistence, external payment provider or concurrent
transactions. Those limits matter when interpreting any observed success.

## Frozen comparison

Four arms use GoPyT 0.1.000/bytecode 2, strict typed Python, strict TypeScript, and
Go. Every fresh session starts from equivalent sources containing an intentional
partial-refund rounding defect. All initial static checks and four identical
public smoke cases pass despite that defect. The defect exists only in the
benchmark subjects; the development application and prior evidence are preserved.

| Task | Requested behavior | Independent acceptance |
|---|---|---|
| A: audit | Check a release handoff against the current contract despite green smoke tests | Find and repair cumulative refund rounding while preserving other rules |
| B: maintenance | Refund original shipping only when a member's entire shipped order has been returned | Retain placement membership across transitions, ignore later membership flags, refund shipping once, preserve rounding and error precedence |
| C: missing policy | Add courtesy returns after a full refund for “loyal customers” | Preserve business sources and ask useful eligibility, benefit and interaction questions; defer readiness |

Each arm has the same authoritative contract, an inaccurate historical handoff
mentioning a nonexistent API, and a clearly labeled demo-only mock script. Public
tools provide source inventories, actual declarations, static checks and limited
black-box checks. The independent controller oracle is not an agent-editable test
and is outside the mounted agent filesystem. A/B agents may add their own tests;
those tests cannot determine controller acceptance.

All prompts explicitly require reading the contract, consulting actual code,
preserving typed categories, asking about missing policy and limiting verification
claims. This is a comparison under substantial common guidance, not an estimate
of failure rates under casual, unguided prompting. The mock is labeled; resisting
an unmarked or maliciously persuasive fake test remains untested.

The protocol fixed four languages × three tasks × two repetitions, one model
(`gpt-6-astra`, high reasoning), 480 seconds and 60 observed tool items per session.
Four sessions run concurrently per block, one per language. Launch order rotates;
the seeded block order is A2, A1, C1, B2, C2, B1. Sessions receive no prior trial
feedback and no repairs or replacement attempts. Token use is observed, not
hard-capped. Provider caching, training exposure and service routing are uncontrolled.
Independent review calls overlapped later measured blocks without giving agents
feedback; shared host/provider load was not controlled. Any workflow timings are
descriptive secondary observations.

The CLI is 0.153.4; comparator tools are Python 3.14.7/mypy 2.3.0,
TypeScript 5.9.3/Node 24.18.0, and Go 1.26.0. Declaration tooling differs: GoPyT
uses its semantic context, Python parsed declarations after mypy, TypeScript
compiler-emitted declarations, and Go documentation after checking. Go does not
have the same compiler-enforced exhaustive enum-switch behavior. This comparison
measures each package as delivered; it has no context-tooling ablation.

| Business source | Files | Lines | Bytes |
|---|---:|---:|---:|
| GoPyT | 20 spec/implementation | 565 | 14,934 |
| Python | 11 including package initializer | 308 | 9,738 |
| TypeScript | 10 | 213 | 9,231 |
| Go | 10 | 332 | 7,748 |

Line counts reflect formatting and explicit declarations, not independent
complexity measurements. All arms contain ten meaningful business modules.

## Evidence and interpretation rules

A/B source acceptance combines static checking, the independent behavioral oracle
and source-integrity review. Session completion and budget censoring are separate.
C's oracle deliberately matches the unchanged seeded baseline: its outputs show
preservation, **not correct refund business logic**. Useful C clarification must
also satisfy all four frozen review dimensions.

Each evaluation exercises 1,117 scenarios / 6,713 command snapshots. Oracle
development includes 15 literal sentinels, mutation probes and 11 unit tests;
baseline transport validation checks 28 malformed inputs per arm. These are
coverage checks, not independent agent observations. The statistical unit is one
fresh session, with only two observations per language/task cell.

Verification claims receive separate supported, unsupported, contradicted or
unknown judgments using retained commands, outputs and source identities. Broad
release claims are distinct from narrow test results. Review checks whether an
agent treated mock-only output as business evidence or introduced an invented
API/policy. Reviewers use the same model family; their semantic judgments are not
external human ground truth or a proof of absence of hidden problems.

Bubblewrap restricts local filesystem/process visibility; direct and child-process
canary/evaluator reads and process-root escapes were probed. The model service
requires networking, so this is not an egress firewall or universal adversarial
security proof. Temporary authentication is removed after each session and is
excluded from retained evidence.

Four excluded setup probes retain DNS, missing code-host and nested sandbox
failures, followed by a successful file-write/isolation probe. The first frozen
campaign, [reliability-v1](agent-evidence/2026-09-06-reliability-v1/), was rejected
before measured execution because generated receipt collection could follow a
symlinked parent. All 24 IDs have explicit not-started dispositions; they are zero
measured attempts, not 24 agent failures. The corrected v2 passed independent
preflight review before execution. Historical evidence was not overwritten.

## Results

| Language | A: audit | B: maintenance | C: useful clarification | Trials with unsupported / contradicted / unknown final claims |
|---|---:|---:|---:|---:|
| GoPyT | 2/2 | 2/2 | 2/2 | 0 / 0 / 0 of 6 |
| Python | 2/2 | 2/2 | 2/2 | 0 / 0 / 0 of 6 |
| TypeScript | 2/2 | 2/2 | 2/2 | 0 / 0 / 0 of 6 |
| Go | 2/2 | 2/2 | 2/2 | 0 / 0 / 0 of 6 |

All 24 sessions completed within the frozen limits; none was censored, replaced
or repaired by the controller. Source-integrity review passed all 24. No mock
reliance or invented API/policy was observed in any final submission. All 24
handoffs contained verification claims; empty claims were not counted as success.
Review classified 201 handoff/final-text records as supported: GoPyT 53, Python
47, TypeScript 45, Go 56. Supported proportions were therefore 53/53, 47/47,
45/45 and 56/56 respectively, with zero unsupported, contradicted or unknown.
These reviewer-segmented records are not equally sized or independent observations;
their counts do not rank languages.

Each of the 16 A/B submissions passed 1,117 independent business scenarios / 6,713
snapshots: 17,872 scenario evaluations / 107,408 snapshots across implementation
trials. The eight C submissions reproduced 8,936 baseline scenarios / 53,704
snapshots and satisfied all four clarification dimensions. The combined 26,808 /
161,112 totals mix implementation acceptance with baseline preservation; they are
not a single business-correctness score.

Source correctness did not require error-free development. For example,
[A-Go-r2 review](agent-evidence/2026-09-06-reliability-v2/reviews/trial-a-go-r2.json)
records a wrong initial tax fixture, a mistaken interim test count and a vet
failure, all corrected and honestly described before final handoff.
[B-Go-r1](agent-evidence/2026-09-06-reliability-v2/reviews/trial-b-go-r1.json)
also corrected a test expectation.
[C-Python-r2](agent-evidence/2026-09-06-reliability-v2/reviews/trial-c-python-r2.json)
ran a real diagnostic exposing the existing split-refund bug, reported exit 1 and
preserved the business source while asking about missing courtesy-return policy.
These are recoveries and calibrated failure reports, not fabricated passing tests.

| A/B workflow, four trials per language | Wall seconds, median [range] | Observed tool items, median [range] | Reported input tokens, median [range] | Reported output tokens, median [range] |
|---|---:|---:|---:|---:|
| GoPyT | 285.29 [252.08–303.63] | 19.5 [14–23] | 283,505 [251,801–312,178] | 6,056.5 [5,157–6,298] |
| Python | 250.72 [175.70–300.29] | 14 [11–15] | 168,455.5 [133,738–195,733] | 5,664 [4,491–6,673] |
| TypeScript | 271.51 [242.67–293.78] | 14 [10–17] | 177,471.5 [166,525–236,464] | 5,260 [4,542–5,766] |
| Go | 260.78 [221.58–348.98] | 16 [12–18] | 209,508 [178,741–300,949] | 6,012 [4,892–7,837] |

Reported input tokens include repeated and cached input, not unique context size
or a billed-cost estimate. Cached-token values and all C resource measurements
remain in [summary.json](agent-evidence/2026-09-06-reliability-v2/summary.json)
and [trials.csv](agent-evidence/2026-09-06-reliability-v2/trials.csv). None of these
numbers measures application execution performance or proves bounded context.

## Validation and retained evidence

The final scoped validation command passed **48 tests**: 11 oracle, 20
receipt/isolation/session/runner, four TypeScript transport, and 13 analysis
regressions. It did not restart earlier runtime or release audits. Analysis
review fixed inconsistencies that could otherwise allow a summary to trust a
contradictory pass flag; regression tests include tampered outputs, inflated
counters, missing reviews and censored-but-correct source submissions.

[evidence-check.json](agent-evidence/2026-09-06-reliability-v2/evidence-check.json)
reconciles all 24 dispositions and distinct session IDs, raw event usage/tool
counts, frozen source hashes, final source inventories and raw oracle hashes and
counts, with zero errors. Controller evaluation used clean source copies and
fresh builds. This reconciliation is not cryptographic authentication or a
second independent proof of the oracle's business interpretation.

Per-trial reviews, prompts, raw transcripts, handoffs, diffs, source snapshots,
controller outputs and failed development checks are retained. The comparator
authors cross-reviewed the other comparator arms; the reviewer of GoPyT A/B had
authored the original development application, and the C reviewer authored the
oracle. These relationships are disclosed in each review and limit independence.
Reviewers did not repair measured submissions or give them feedback.

GoPyT runtime, release 0.1.000 and bytecode version 2 are unchanged. Prior sealed
pilot, development and runtime evidence remains historical evidence, not new
validation claimed by this experiment.

## What remains to demonstrate

The seeded baseline is already a useful counterexample to a stronger claim:
**GoPyT's compiler accepts a well-typed implementation that violates a prose
business rule.** Compiler-derived context establishes available declarations and
recorded contracts; it does not prove that the arithmetic satisfies those
contracts. A source-bound receipt establishes which checks ran against which
inputs, not that those checks cover every requirement.

To address RP-001 directly, the next experiment should freeze larger, meaningful
repositories and changes with known semantic dependencies, then measure missed
requirements and retrieved context as repository size grows. A separate ablation
should compare the same GoPyT tasks with and without compiler-derived context and
receipts. Include more models and repetitions after the harder contracts and
oracle are reviewed. Keep unsupported confidence as its own outcome; do not
replace it with speed, test counts or an aggregate score chosen after results.
