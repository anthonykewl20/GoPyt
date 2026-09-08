# Next stage: order lifecycle and source-bound tooling

Bounded phase 1, 2026-09-05. Preserve all sealed pilot/runtime evidence. Release
0.1.000 and bytecode 2 remain unchanged. This phase contains no measured sessions.

## Frozen scope

Build the [order lifecycle v1](../benchmarks/order_lifecycle/CONTRACT.md) transition
engine in GoPyT and independently validate it. Implement developer tooling outside
the locked four-command CLI to export compiler-derived project context and execute
checks/tests with source-bound JSON receipts. Document and test failure/staleness
semantics. Independent implementation, oracle authorship and review are required.

Tooling schema v1: context is available only after successful compiler checking
and bytecode validation. Include exact public declarations (including generic
signatures, contracts and types), declared imports, package dependencies, resolved
call edges, source locations, toolchain/source/artifact hashes. Clearly distinguish
static compilation from runtime test outcomes and business acceptance. A receipt
records actual commands/operations, exits, diagnostics, executed test names and
source identity. Failed checks produce failure receipts with no successful context
or stale artifact claim. Verification recomputes content identity; it is integrity
checking, not a signature, proof of honesty, or a business-correctness certificate.
Context diff compares public declarations to surface added/removed/changed APIs.
No invented summaries of requirements; provenance links refer to compiler input.

## Subsequent measured phases (not authorized as part of phase 1 scope)

Before new trials: grow meaningful multi-order/multi-SKU and persistent workflows;
create equivalent typed Python and strict TypeScript subjects and context support;
freeze maintenance requests, allowed changes, ambiguity rubric, stale-document
precedence, public/withheld tests, seeds, baselines, toolchains and resource budgets.
Review equivalence and compiler/oracle gates independently. Establish and probe an
OS evaluator read boundary for both agent tools and executed subject processes.
Record actual source/module/dependency size; line inflation is not realism.

Freeze a separate campaign manifest before measured execution. Compare tooling
available/unavailable as explicit ablations. Start with larger-repository fresh
maintenance, then add repetitions and models using separately frozen schedules.
Retain every scheduled failure, censoring, infrastructure attempt and replacement;
never repair or replace an attempt invisibly. Missing requirements require
clarification, not policy invention. Report controller acceptance separately from
agent verification claims. Runtime throughput is a separate experiment. This phase
provides no new estimate of agent effectiveness or GoPyT superiority.
