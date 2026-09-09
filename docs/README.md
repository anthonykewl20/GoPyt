# Current repository baseline

See the [architecture checklist review](architecture-checklist-review-2026-09-09.md),
[atomic data-backend validation](architecture-validation-2026-09-09.md),
[release validation](release-baseline.md), [security operation](../SECURITY.md),
[editor support](editor.md) and [roadmap](ROADMAP.md). Earlier reports below may
refer to evidence archives outside this language-only checkout.

# GoPyT v0 documents

Read in this order. Later files must not contradict earlier locks without an amendment.

| File | What it is | Status |
|------|------------|--------|
| [charter.md](charter.md) | Why it exists (D1–D14) | locked |
| [ai-surface.md](ai-surface.md) | What an agent may emit (anti-slop) | locked |
| [security.md](security.md) | Compiler/VM security (S30) | locked |
| [evolve.md](evolve.md) | Self-evolution (S31) | locked |
| [hardening.md](hardening.md) | Observe / analyze / harden (S32) | locked |
| [spec.md](spec.md) | Language decisions (S1–S32) and precedence | locked |
| [grammar.ebnf](grammar.ebnf) | Tokens and syntax | locked |
| [stdlib.md](stdlib.md) | Closed standard library | locked |
| [bytecode.md](bytecode.md) | VM and opcodes | locked |
| [diagnostics.md](diagnostics.md) | `GOPYT_E*` codes and repairs | locked |
| [interview-tree.md](interview-tree.md) | Human → `spec/` questions | locked |
| [elaborator.md](elaborator.md) | `spec/` → `impl/` / `test/` | locked |
| [lockfile.md](lockfile.md) | `gopyt.toml` / `gopyt.lock` | locked |
| [implementer.md](implementer.md) | No-guess runtime/CLI/limits/HTTP | locked |
| [fmt.md](fmt.md) | Canonical formatter | locked |
| [conformance.md](conformance.md) | Must-accept / must-reject | locked |
| [agent-guide.md](agent-guide.md) | Non-guessing implementation protocol | locked |
| [audit.md](audit.md) | v0 completeness matrix and release gate | locked |
| [validation.md](validation.md) | What is proven vs what needs prototypes | locked |
| [runtime-amendment-2026-09-05.md](runtime-amendment-2026-09-05.md) | Explicit runtime and authoring closure rules | normative amendment |
| [storage-amendment-2026-09-05.md](storage-amendment-2026-09-05.md) | Durable package storage and atomic conditional updates | normative amendment |
| [storage-cache-amendment-2026-09-05.md](storage-cache-amendment-2026-09-05.md) | Bounded read cache, freshness and failure behavior | implementation amendment |
| [http-input-amendment-2026-09-05.md](http-input-amendment-2026-09-05.md) | HTTP input deadlines, framing and transport behavior | normative amendment |
| [app-validation-plan.md](app-validation-plan.md) | First real application validation scope and work sequence | application plan |
| [benchmark-methodology.md](benchmark-methodology.md) | Independent acceptance and repeatable performance measurements | measurement protocol |
| [app-validation-report-2026-09-05.md](app-validation-report-2026-09-05.md) | Ticket app, fixes, repeated measurements and remaining weaknesses | application results |
| [runtime-followup-plan-2026-09-05.md](runtime-followup-plan-2026-09-05.md) | Memory attribution and controlled runtime optimization | follow-up plan |
| [runtime-followup-report-2026-09-05.md](runtime-followup-report-2026-09-05.md) | Runtime fixes, cache hit/miss measurements and extended soak | follow-up results |
| [agent-effectiveness-benchmark-plan.md](agent-effectiveness-benchmark-plan.md) | Fresh-session maintenance and clarification comparison with typed Python and strict TypeScript | pilot protocol |
| [agent-effectiveness-report-2026-09-05.md](agent-effectiveness-report-2026-09-05.md) | Eighteen fresh-agent trials, independent acceptance, clarification review and limits | measured pilot results |
| [agent-next-stage-plan-2026-09-05.md](agent-next-stage-plan-2026-09-05.md) | Frozen bounded order-lifecycle/tooling phase and prerequisites for later measured campaigns | development plan |
| [project-context.md](project-context.md) | Compiler-derived interfaces, source-bound check/test receipts and replay | developer tooling |
| [obligations.md](obligations.md) | Stable business-obligation ids, executable representations, verification scope, stale evidence and change impact | developer tooling |
| [agent-obligations-report-2026-09-06.md](agent-obligations-report-2026-09-06.md) | Obligation tooling, independent challenge results, larger validated subject, footprint measurements and an unexecuted ablation protocol | development results |
| [agent-next-stage-report-2026-09-05.md](agent-next-stage-report-2026-09-05.md) | Order lifecycle acceptance, tooling validation and retained development evidence | development results |
| [agent-reliability-report-2026-09-06.md](agent-reliability-report-2026-09-06.md) | Twenty-four fresh-agent trials across GoPyT, Python, TypeScript and Go; business correctness, clarification and verification-claim review | measured reliability results |
| [implementation-closure-2026-09-05.md](implementation-closure-2026-09-05.md) | Current fixes, independent review and release evidence | current audit |
| [implementation-audit-2026-09-05.md](implementation-audit-2026-09-05.md) | Implementation findings, repairs, test evidence and remaining release gaps | audited |
| [gopyt-native-ai-programming-languge.md](gopyt-native-ai-programming-languge.md) | Research notebook (not the spec) | historical |
| [peon-runtime-amendment-2026-09-06.md](peon-runtime-amendment-2026-09-06.md) | Local model native, artifact format, resource and training boundaries | narrow runtime amendment |
| [peon-implementation-report-2026-09-06.md](peon-implementation-report-2026-09-06.md) | Running runtime/apps, retained training failures and remaining acceptance | implementation evidence; incomplete full design |
| [peon-spec-acceptance-2026-09-06.md](peon-spec-acceptance-2026-09-06.md) | Full-design requirement matrix, failed quality gates and dependency order | full-spec acceptance not met |
| [peon-agent-v1.md](peon-agent-v1.md) | Compiler-derived catalog and bounded stdio interface for other agents | implemented; independently reviewed |
| [peon-tokenizer-v2.md](peon-tokenizer-v2.md) | Owned byte-BPE, bound model envelope and legacy compatibility | implemented; mechanism independently reviewed |
| [peon-worker-v1.md](peon-worker-v1.md) | Operator-built native artifacts and compiler-free inference deployment | implemented optional path |
| [peon-evidence-v1.md](peon-evidence-v1.md) | Durable local execution facts with payload-free records | implemented bounded journal |
| [peon-plans-v1.md](peon-plans-v1.md) | Source/data/authority-bound read-only operation plans | bounded implemented protocol |
| [peon-model-card.md](peon-model-card.md) | Checkpoint architecture, training and measured local footprint | experimental model; failed intent gate |
| [peon-checkpoint.md](peon-checkpoint.md) | Exact Peon continuation and running training | retained checkpoint history; see current delegation status |
| [peon.md](peon.md) | Self-contained specialist: development, application analytics, training and controlled evolution | implementation design; not runtime capability |
| [peon-glm-goal.md](peon-glm-goal.md) | Training handoff blocked until implementation/application acceptance | withheld; not started |
| [peon-oss-assessment.md](peon-oss-assessment.md) | Pinned esp32-ai source inspection and bounded adoption experiment | source assessment; not validated |
| [The GoPyT Journal](../JOURNAL.md) | The project's origins, development milestones, experiments, lessons and open questions | living historical journal; non-normative |

v0 **language docs** are locked. A Python-hosted compiler and VM now exist in
`gopyt/`, with a launcher in `scripts/gopyt`. The current closure audit records implementation and validation results; the
first audit is retained as history. Empirical claims are limited to the
experiments actually run.

## Current Peon coding track

Start with [delegation v2](peon-delegation-v2.md), [training and automation v2](peon-training-v2.md),
and [implementation status](implementation/peon-delegation-v2/STATUS.md). Earlier
model cards, checkpoints, audits, research PDFs and retained experiment trees are
versioned evidence. Their numbers describe the named snapshot, not the current
delegation policy. Shop/retail contracts remain independent of this coding track.

Optional external coding evaluation: [Peon with the installed VulcanBench](peon-vulcan-v1.md). Its dependencies stay in the benchmark environment; Peon serving remains separate.

Full experimental coder training: [verified repair data to LoRA to paired evaluation](peon-coder-training-v1.md). Use `python3 -m peon.train coder --help`; heavy dependencies remain isolated in the optional training environment.

- [Peon ZCode coder operations](peon-zcode-automation-v1.md): scheduled training, benchmarks, dogfooding, calibration and reporting.
- [Peon verified action distillation](peon-distillation-v1.md): teacher provenance, executable filtering and student evaluation.

- [Native boundary amendment](native-boundary-amendment-2026-09-09.md): full-range cancellable sleep, optional-provider errors and malformed policy handling.

- [Delegated resource authority](resource-authority-amendment-2026-09-09.md): host-issued attenuated resource handles, revocation and native enforcement.

- [Verified request identity](request-identity-amendment-2026-09-09.md): operator-provisioned subjects, tenant-bound sessions, per-request authorization, expiry and revocation.

[Transaction outcome and cancellation amendment](transaction-outcomes-amendment-2026-09-09.md)
defines publication, acknowledgment and receipt-based reconciliation boundaries.

[Exact toolchain compatibility](toolchain-compatibility-amendment-2026-09-09.md)
binds format-3 artifacts and locks to runtime sources and specifies upgrade steps.

[Pinned build inputs and wheel verification](release-inputs-amendment-2026-09-09.md)
defines current build pins, reproduction checks and remaining release gates.

Release provenance: [workflow, verification and withdrawal policy](release-provenance.md). Signed end-to-end qualification and complete component/adaptation inventory remain open under #23.

[Installed release component inventory](component-inventory.md) retains bundled metadata and its current coverage limits under #23.

[Finite binary64 amendment](finite-f64-amendment-2026-09-10.md) defines literal
rounding, overflow and nonfinite rejection at bytecode and VM boundaries.

[Exact monetary values](money-amendment-2026-09-10.md) defines checked fixed-point
arithmetic, explicit rounding, currency validation and decimal text boundaries.

[Typed time amendment](time-amendment-2026-09-10.md) defines nanosecond timestamps,
durations, explicit-offset serialization, clock origins and anomaly behavior.

[Combined numeric/time qualification](numeric-time-qualification.md) maps issue
#17 to its implementation, independent oracles, full data trials and limits.

[Parallel admission and deadlines](parallel-admission-amendment-2026-09-10.md)
adds a shared VM worker bound, inherited deadlines and explicit overload behavior;
blocking-I/O and graceful-drain qualification under #13 remains open.

[Deadline boundary specification](deadline-boundaries.md) covers every shipped
native, generated conversion form and structured cleanup boundary, including
noninterruptible host work and commit ambiguity.
