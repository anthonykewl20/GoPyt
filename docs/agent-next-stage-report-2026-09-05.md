# Order lifecycle and compiler context: bounded phase 1

Implemented a ten-module order lifecycle application and compiler-derived project
context with source-bound check/test receipts. Independent development acceptance
passed 3,908 scenarios, 23,116 command snapshots and 28 transport rejection probes.
No new agent-effectiveness trials were conducted. Release remains 0.1.000 and
bytecode remains version 2; existing compiler/runtime semantics are unchanged.

The [contract](../benchmarks/order_lifecycle/CONTRACT.md) and
[phase methodology](agent-next-stage-plan-2026-09-05.md) were hashed before
implementation. Separate agents authored the GoPyT implementation, Python oracle,
and adversarial review. They share a workspace and model family; this establishes
separate authorship, not organizational independence or an evaluator read boundary.
The oracle author did not inspect application bodies. The reviewer inspected
application bodies after oracle authorship was complete.

## Application behavior and scope

[examples/orders](../examples/orders/README.md) implements reservation, quoted
member discounts/tax/shipping, payment, shipment, cancellation, cumulative partial
refunds and stock returns. Version conflicts precede business validation, failed
commands preserve every state field, and exhaustive internal categories carry
states and outcomes across modules. Partial refunds use cumulative entitlement
so splitting refunds cannot change the final amount. The adapter only translates
the strict JSON-lines transport; business transitions execute as GoPyT bytecode.

The application has 565 spec/implementation lines across ten meaningful modules,
compared with 143 lines across four modules in the earlier GoPyT pilot. This adds
interacting stateful rules, but remains a small single-order, single-SKU pure
transition engine. There is no durable database, concurrent warehouse access,
payment provider, multi-order workflow or large-repository claim. Its scoped
synthetic business policy is explicit in the frozen contract; it is not inferred
from a real merchant's undocumented requirements.

## Tooling behavior and verification

[The new developer tool](project-context.md) exports exact checked public
declarations, generic signatures, types, contracts, effects, import allowlists,
package dependencies and compiler-resolved calls. Context diff surfaces public
declaration additions/removals/changes without inventing semantic explanations.
It lives outside the locked four-command GoPyT CLI.

A receipt binds compilation and actual test results to a copied source/dependency
graph and the emitted, decoded bytecode. It records failed compilation, runtime
traps, internal test errors, unexecuted tests and zero-test runs distinctly.
Verification replays the recorded operation on current sources; changed inputs or
outcomes fail comparison. Receipts use new output paths and never silently replace
previous attempts. Compilation/test success remains separate from independent
business acceptance. Hashes are integrity checks, not authentication or proof of
historical honesty. Tests use a fresh temporary package root and retain effects;
external environment and provider behavior are not frozen.

The [orders test receipt](agent-evidence/2026-09-05-next-stage-v1/orders-tests.json)
records nine successful tests, 26 public declarations and 55 resolved edges. Its
[replay](agent-evidence/2026-09-05-next-stage-v1/orders-replay.json) matches. Package
lock digest is `sha256:68139bb36823480399a0fb8d031e290f1b2a734d5acb532baea10080b271f5be`;
artifact hash is `sha256:2a197cf0afe1536ef4149ec9e632b3477304e9a2855d7c7647bf29aaf75bdb8a`.
The receipt's broader graph digest also includes lock-file bytes and has a
different value by design.

## Executed checks and retained failures

- Full Python-host regression: **490 tests passed**, zero failures/skips, Python
  3.14.7 on this Linux host. This includes 13 new context/receipt unit tests.
- Fixture harness: two tests passed over 54 fixture packages. C027's bytecode
  phase and C040's evolve phase retain their documented harness exclusions;
  this is not a claim that every fixture phase executes through that harness.
- Standard library synchronization: all 20 modules match.
- Fresh application copy: fmt/check/test exited 0; nine GoPyT tests and nine
  Python public tests passed.
- Independent context review: **11 adversarial tests passed**, including generic
  traits, effectful agents, HTTP dispatch, dependency identity, source/receipt
  tampering, partial test failure, and internal-error retention.
- Independent oracle: **12 unit tests passed**, eight literal sentinels and six
  deliberately incorrect policy variants detected. Controller acceptance passed
  all 3,908 scenarios / 23,116 snapshots and all 28 transport probes. Actual
  output exactly matches expected bytes. These are coverage counts, not agent
  success-rate observations.

[Development acceptance 01](../benchmarks/order_lifecycle/evidence/development-01/report.json)
remains unchanged. Receipt error-handling work subsequently changed one tooling
file, which the final archival identity check detected. A separately retained
[development acceptance 02](../benchmarks/order_lifecycle/evidence/development-02/report.json)
checks final source identity; neither run is a measured agent trial. The earlier
tooling source was reconstructed from the exact edit and its bytes verified
against acceptance 01's recorded hash before archival.

Development failures are retained. Application authoring encountered an initial
Python generator error, invalid CLI arguments, enum-condition parser ambiguity,
a test-module layout error and expected stale-lock diagnostics; the application
[development record](../examples/orders/DEVELOPMENT.md) lists their resolutions.
The first tooling unit run had 13 fixture setup errors from an incorrect test
module header. Independent review found sibling-dependency path normalization,
manifest traversal/capture consistency and internal-error receipt defects; these
were fixed before final verification. Initial/final test logs are archived. Two
subagent turns were reported as capacity failures; the same oracle task resumed
and completed. No measured attempts were replaced or discarded.

The [independent review](../benchmarks/order_lifecycle/review/README.md) found no
remaining reviewed blocker and reconciled acceptance 01's raw outputs, hashes and
counters. Its reproducible checker also covers subsequent acceptance records.
[Phase evidence](agent-evidence/2026-09-05-next-stage-v1/README.md) retains source
snapshots, summaries, receipts, logs and hashes. Prior sealed pilot/runtime
evidence was not changed or re-audited. The workspace has no Git checkout, so no
commit or PR was created.

## Next bounded work

Add meaningful multi-order/multi-SKU and persistent workflows, then equivalent
strict Python/TypeScript subjects and context tooling. Freeze maintenance requests,
stale-document precedence, ambiguity requirements, baseline/oracle/tool versions,
ablations, schedules and resource budgets before any measured session. Establish
and probe evaluator isolation for both direct tools and executed subject code.
Start fresh-session maintenance on larger repositories before adding models and
repetitions. Keep every failure and distinguish agent claims from controller
checks. Agent effectiveness and application runtime performance remain separate;
this phase establishes neither superiority nor production readiness.
