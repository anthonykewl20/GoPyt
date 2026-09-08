# Development boundary probe

`boundary_probe.py` compares a candidate with a baseline authenticated by an
existing guard bundle. It reports behavioral differences for review. It is not
a guard receipt, candidate approval or an independent business oracle.

```bash
python3 benchmarks/language_guard/calibration/boundary_probe.py \
  --engine /trusted/gopyt \
  --baseline /trusted/approved-policy \
  --candidate /work/candidate-policy \
  --bundle /trusted/approved-bundle.json \
  --pin APPROVED_BUNDLE_SHA256 \
  --out /work/new-probe-report.json
```

Supply the bundle pin from outside the candidate tree. The baseline must match
every bundle file hash, including its lock file. Candidate protected files and
contract-helper meaning remain checked. Both trees are compiled from bounded
disposable snapshots, and the baseline must pass the original acceptance cases.
Build artifacts are not silently ignored. The command does not update sources,
bundles or approvals. Existing output files are never overwritten.

## Heuristic mode

The default mode interleaves ordinary scalar boundaries with targeted argument
combinations. It extracts literal and constant-arithmetic values from the AST,
and derives candidate parameter values from comparisons such as `stock + 7 ==
49`. Combining parameter values can reach joint or nested branches. Boolean
choices preserve bool/int distinctions. Strings and comments are not constants.

Limits are 32 successful seeds, 128 scalar constants, 16 directed values per
parameter, and 2,000 generated probes by default. Successful symbols receive
seed representation before extra rows fill remaining seed slots. Reports expose
truncation and symbols absent from the generated vectors. Functions represented
only by trap cases have no successful heuristic seed; finite mode can include
them. `--limit` accepts 1 through 10,000.

This search is not exhaustive. It can miss nonlinear relationships, aliases,
helper-dependent branches, large combinations or values outside its budgets.
No differences in this mode means only that this sample found no differences.

## Complete finite-domain mode

Add `--domains /trusted/domains.json` to enumerate a caller-declared domain.
The domain must map exactly the acceptance symbols to either argument axes:

```json
{"policy.reserve": [[40, 41, 42, 43, 44], [15, 16, 17, 18, 19]]}
```

or explicit input tuples:

```json
{"policy.reserve": {"tuples": [[42, 17], [10, 3]]}}
```

Axes enumerate their complete Cartesian product. Tuples enumerate exactly the
listed vectors. Duplicate values/vectors are deduplicated. Every argument must
match the compiled scalar signature. Empty domains, unknown/missing symbols,
invalid values and duplicate JSON keys are refused. The complete deduplicated
domain must fit `--limit`; otherwise the command refuses rather than samples.
For example, use `--limit 10000` for the 8,393 retained retail input pairs.

`domain_fully_compared: true` means every declared tuple was evaluated in both
implementations. `domain_equivalent` says whether their observed return/trap
outcomes match throughout that domain. It does not cover inputs outside it or
establish that the baseline meets business requirements. Original acceptance
results are separate and may include inputs outside the declared domain.

Protect and review the domain file separately from the candidate. A candidate
must not define the domain used to assess its own completeness. This tool does
not approve a domain or enforce organizational authorization policy.

## Outcomes, bounds and interpretation

Version 2 compares baseline traps as well as returns. It counts every mismatch,
retaining at most 20 examples and reporting truncation. Identical traps count
as matching outcomes; this is not a guarantee of successful completion for
those inputs. Unlike version 1, baseline traps are not skipped.

The CLI runs in a child with a 120 second parent timeout, a 512 MiB Linux
address-space limit, and 50 ms cooperative deadlines per VM call. Cancellation,
resource exhaustion or worker failure does not produce a completed report.
The trusted executor and engine installation still require protection; this
is not an OS security sandbox. CLI success means a diagnostic report was
written, not that the candidate passed or should be approved.

Intentional improvements can legitimately differ from the baseline. Validate
counterexamples against requirements before adding approved cases. No new
runtime dependency or engine pin is introduced by this development command.

[Coverage R&D evidence](../../../docs/agent-evidence/2026-09-09-probe-coverage/REPORT.md)
