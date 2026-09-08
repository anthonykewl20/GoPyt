# Agent effectiveness pilot methodology

Status: protocol for the first small-repository pilot. A protocol, working
compiler, or successful deterministic repair script is not evidence that one
language makes agents more effective. Outcomes must come from retained fresh
agent trials. This study tests a bounded part of the intent-to-implementation
workflow in [the charter](charter.md), [the AI surface](ai-surface.md), and
[the validation requirements](validation.md).

## Question and scope

Can a fresh coding agent correctly modify a small modular quotation application,
propagate an explicit contract change across modules, and defer an ambiguous
business request without silently inventing policy? Measure these behaviors in
GoPyT, typed Python, and strict TypeScript using one fixed model and configuration.

The planned pilot has **18 trials**: three languages, three tasks, and two fresh
runs of each language/task combination. Each trial starts from its language's
unchanged baseline; tasks are independent rather than cumulative. Language order
is balanced within repetitions and the execution schedule is frozen before
trials. Record the schedule and its seed rather than choosing the next language
after seeing an outcome.

This is an instrumented feasibility pilot. It does not establish superiority,
performance on massive repositories, benefits across models, or production
reliability. Two repetitions per cell are insufficient for stable success-rate
estimates. The existing storage/HTTP benchmarks measure runtime behavior, not
agent effectiveness.

## Equivalent subjects and task contract

All subjects implement the same small application through corresponding
`model`, `pricing`, `quote`, and `checkout` modules, with a fixed JSON-lines CLI
adapter. The adapter, checker configuration, execution harness and evaluator are
not agent-editable. Each subject has public smoke checks and a language-specific
guide with the build/check/test commands. Strict Python and TypeScript checking
must remain enabled; their public result/category types and exhaustive consumers
must represent the same distinctions as GoPyT. Do not weaken a comparator into
an untyped implementation to make GoPyT appear safer.

The common baseline accepts `quantity`, `unit_cents`, `customer_kind`, and
`shipping_zone`. Quantity is an integer from 1 through 100, unit price is integer
cents from 0 through 1,000,000, customer kind is `retail` or `member`, and shipping
zone is `local` or `remote`. The response has exactly `outcome`, `subtotal_cents`,
`discount_cents`, `shipping_cents`, and `total_cents`. Member discount is
`floor(subtotal_cents * 500 / 10000)`. Shipping is 500 cents locally or 1,500 cents
remotely, with free shipping at a post-discount subtotal of at least 10,000 cents.
Invalid requests produce `invalid` and zero amounts. The [versioned business contract](../benchmarks/agent_eval/contracts.md) and
evaluator fixtures freeze exact input-shape, type, and validation precedence
rules; all baselines must pass before agent trials start. The scored transport
corpus uses safe lexical JSON integers. Python, TypeScript and GoPyT can differ
on other number spellings and ranges; these transport differences are excluded,
not silently counted as agent failures.

| Task | Requested change | Required evidence |
| --- | --- | --- |
| A: pricing | Members receive 10% at a subtotal of at least 20,000 cents, otherwise the existing 5%; preserve other behavior. | Static checks and withheld behavioral cases, especially rounding, threshold boundaries and unaffected categories. |
| B: category and error contract | Add `nonprofit` with a 7% discount; invalid quantity produces `invalid_quantity`, taking precedence over other invalid business fields. | Updated public types and all affected consumers, static checks, behavioral coverage of the new category/error and preserved old outcomes. |
| C: ambiguous shipping | “Give loyal customers better shipping.” No eligibility, benefit or stacking policy is supplied. | A clarification artifact and unchanged application code; independently assess whether questions identify the missing business decisions. |

The task-specific request supplies authorization to change the public contract
where required in A/B. In GoPyT, public spec amendments precede matching
implementation changes. No trial receives unstated withheld business requirements:
withheld cases exercise the frozen contract, not a second private specification.

C is **clarification only**, not a shipping implementation task. The pilot does
not supply a simulated user's answer or score a completed shipping feature.
Guessing a policy does not count as successful intent handling, even if the
hypothetical result happens to be reasonable. Structural artifact checks can
verify that an answer exists and that source was preserved; they cannot judge
whether its questions are meaningful. A reviewer separately assesses eligibility,
benefit, interaction/stacking, and avoidance of invented commitments. That review
is explicitly a **model assessment**, not external human adjudication or an
objective semantic oracle. Preserve the artifact, rubric and assessment rationale.

## Freshness, model configuration and budgets

Before scored trials, freeze a campaign manifest containing:

- exact model identifier, reasoning configuration, agent CLI version and command;
- subject, guide, prompt, runtime, adapter, public-test and evaluator hashes;
- checker/compiler/runtime versions and configurations for each language;
- trial matrix, execution order/seed, wall deadline and tool-count limit;
- writable paths, provided documentation, evaluator visibility and tool policy;
- scoring rules, clarification rubric and any infrastructure retry policy.

Use fresh subject copies, fresh ephemeral agent sessions and no resumed chat or
previous trial feedback. The local Codex CLI probe reports support for
`--ephemeral`, `--ignore-user-config`, `--json` and `--sandbox workspace-write`;
the runner must retain the actual invoked command and verify supported behavior.
Those flags alone do not prove that account-level service behavior, model-side
caching or every environmental influence has been eliminated.

Use the same model/configuration, wall deadline and tool-count limit for every
language/task cell. Enforce wall and tool-count budgets externally. Record the
actual enforcement event and any in-flight operation or event-delivery overshoot;
do not claim a stronger cap than the runner can enforce. Token usage may only
arrive at turn end. Record reported input/context, output and cached-input tokens,
including their source and availability. Missing usage stays unknown. Do not
call a post-hoc token observation a hard token cap, and do not estimate token
counts from character counts as though they were API-reported usage.

Documentation sizes and model familiarity can differ. Record the initial prompt
and accessible guide sizes; keep business facts and commands equivalent without
pretending that equal characters erase pretrained Python/TypeScript familiarity.
Any GoPyT-specific repair protocol or canonical tooling belongs to the measured
language/tooling package. The pilot cannot attribute a difference uniquely to
syntax, type checking, documentation, or training exposure.

Instrument the runner before scoring. Any setup/smoke runs are labeled excluded
and retained separately; they must not become undisclosed extra attempts on a
scored task. Do not tune a prompt or subject using a failed scored trial and
then silently replace that trial. An amendment starts a separately identified
campaign with new hashes.

## Evaluator independence and visibility

The evaluator is maintained separately from subject implementations, lives
outside the writable project, and is run by the controller only after the agent
stops. Withheld expected outputs or failing withheld cases are never returned as
repair feedback. During a trial, the agent can use the declared static checks
and public tests. Every final subject is evaluated, including incomplete,
budget-limited and agent-declared unsuccessful results.

“Outside the writable project” is **not an adversarial read-isolation guarantee**.
A workspace-write sandbox can permit reads outside that workspace. Furthermore,
agent-edited Python/TypeScript executed by public-test commands may read the host
filesystem even if direct read tools are restricted. Unless an independently
verified OS boundary withholds the evaluator from those executed processes, the
study must label visibility as **cooperative withholding**, inspect traces and
final code for evaluator access, and disclose this limitation. Source hashes
prove which evaluator was used, not that the model could not read it.

Integrity checks reject changes to immutable adapters, test harnesses, checker
settings or unauthorized dependencies, including attempts to disable checks. Review source-level suppression and
contract weakening as well as configuration edits: a new typed variant cannot be
replaced by an unconstrained string merely to evade exhaustive consumer updates.
Preserve both the behavioral outcome and the integrity failure rather than
classifying a bypass as successful implementation. A common behavioral oracle
checks every language through its fixed adapter. Language-specific static checks
are reported separately; a compiler pass alone is never a business-correctness
pass.

## Measurements and retained evidence

The unit of analysis is one fresh agent trial. Retain all scheduled trial IDs,
even if the agent fails to start. Separate infrastructure failures from completed
or censored agent attempts, and disclose replacements or missing cells.

For each trial retain:

- initial/final source inventories and hashes, changed files and source diff;
- exact prompt, model/configuration, session command and execution environment;
- complete structured event stream, tool invocations, visible command results,
  final agent message and any clarification artifact;
- agent-process wall time, observed tool count, reported token usage, exit status,
  deadline/limit events and whether the result was censored;
- final static-check commands, exit codes and diagnostics;
- public-test and withheld-evaluator results, case counts, failures and final
  immutable-file checks, with evaluator time separate from agent time;
- C's mechanical checks and independent rubric assessment as distinct fields.

A/B primary success requires allowed changes, passing static checks and all
required withheld behavioral cases. C reports two separate outcomes: mechanically
valid safe deferral, and the model review's clarification assessment. Do not fold
these unlike outcomes into an unexplained aggregate “accuracy” percentage.

Report first-check diagnostics and repair activity when the event stream makes
those observations available. An agent that checks repeatedly is not necessarily
less effective than one that never checks; final correctness and resource cost
must be read together. Tool counts are not universal reasoning steps, token
usage is not automatically money spent, and agent wall time includes service
latency and local tools. Record actual prices only if independently verified
for the executed configuration; this pilot need not estimate cost.

Present all 18 outcomes by language and task, with raw numerators/denominators,
resource ranges and named failure modes. If reporting resource medians for
successful trials, also report failed/censored trials and their consumed
resources to avoid survivorship bias. Do not extrapolate a two-run cell into a
reliable population confidence statement or claim statistical significance.

## Review and completion gate

Before the first scored run, independently review baseline equivalence, the
oracle's boundary cases, runner isolation claims and the frozen manifest.
Afterward, reconcile scheduled trials against evidence files and hashes;
recalculate counts and summary metrics from the raw records. Review failed
subjects as well as successful ones. Mechanical evidence validation verifies
record consistency, not model honesty or semantic truth.

The pilot is complete only when every planned cell has a retained disposition,
all final available subjects have been evaluated, integrity/usage limitations
are explicit, and the report supports its conclusions from those artifacts.
If model access or isolation infrastructure blocks execution, ship the runnable
harness and an honest unexecuted status. Do not replace fresh-agent observations
with hand-written solutions, replayed successful edits or deterministic repair
scripts.

Larger repositories, more repetitions, alternate models, repeated maintenance
chains, user-answer elicitation, stronger OS evaluator isolation and ablations
of GoPyT's individual features are follow-on studies. They remain untested by
this pilot.
