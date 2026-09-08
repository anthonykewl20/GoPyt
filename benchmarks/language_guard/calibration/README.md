# Real-data guard calibration with local VulcanBench

This is a custom GoPyT calibration campaign using VulcanBench's actual
`run_declarative_verifier` and native `validate-task` CLI. It is not a stock-suite
score, empirical model difficulty estimate, or a new LLM coding run.

Input attribution: Chen, D. (2015), [Online Retail](https://archive.ics.uci.edu/dataset/352/online+retail),
UCI Machine Learning Repository, DOI 10.24432/C5BW33, CC BY 4.0. The raw workbook
is retained at `benchmarks/guarded_refunds/data/online-retail.zip`. This campaign
reads its quantity and unit-price columns independently of the refund fixture's
invoice filtering. Cancellation quantities, zero prices, and negative prices are
kept. Four nonrepresentable subpenny-price rows are counted and excluded.

The target contract is signed line extension in integer pence. This is a newly
authored example of a language contract, not the original retailer's source code
or evidence of payment settlement. Mutations are constructed. Development and
evaluation invoices are disjoint; numeric pairs overlap, and this public data
has been used in prior project experiments.

## Reproduce

Use fresh output paths for every command. The installed VulcanBench Python has
its harness dependencies; the scripts freeze both GoPyT and harness Python
sources before importing them. They do not modify the VulcanBench installation.

```sh
python3 -m benchmarks.language_guard.calibration.prepare \
  --archive benchmarks/guarded_refunds/data/online-retail.zip \
  --provenance benchmarks/guarded_refunds/data/provenance.json \
  --out /tmp/new-guard-data

/home/soultransit/devtony/VulcanBench/.venv/bin/python \
  benchmarks/language_guard/calibration/run.py \
  --gopyt /home/soultransit/devtony/gopyt \
  --vulcan /home/soultransit/devtony/VulcanBench \
  --data /tmp/new-guard-data --out /tmp/new-guard-run

/home/soultransit/devtony/VulcanBench/.venv/bin/python \
  benchmarks/language_guard/calibration/transfer.py \
  --prior /tmp/new-guard-run --data /tmp/new-guard-data \
  --out /tmp/new-guard-transfer

/home/soultransit/devtony/VulcanBench/.venv/bin/python \
  benchmarks/language_guard/calibration/full_development.py \
  --prior /tmp/new-guard-run --transfer /tmp/new-guard-transfer \
  --data /tmp/new-guard-data --out /tmp/new-guard-full
```

`export_vulcan.py` exports a native task with a starting bug, gold repair,
protected guard check and real-data oracle. Optional `--bundle` selects an
exact-contract calibrated bundle instead of the initial 128-case bundle.

```sh
python3 benchmarks/language_guard/calibration/export_vulcan.py \
  --prior /tmp/new-guard-run --data /tmp/new-guard-data \
  --out /tmp/new-vulcan-task
/home/soultransit/devtony/VulcanBench/.venv/bin/vulcanbench \
  validate-task /tmp/new-vulcan-task --sandbox local
```

Task checks intentionally reference an absolute, independently controlled frozen
GoPyT toolchain. Relocation requires a new export/validation. Candidate Python or
shell is not executed by the custom Runner. Native validation executes the
trusted exported Python checks locally; it is not Docker validation. No provider
is contacted and no model identity or leaderboard row is fabricated.

## Applying the calibration

Protect an exact contract before selecting acceptance cases. For small scalar
corpora, deduplicate by the complete input tuple and use every development tuple
when it fits the 10,000-case and 2 MB bundle limits. The observed corpus has
7,494 development tuples and fits. For larger corpora, retain a fast coverage
suite plus separately reported full replay batches. Check each batch; one pass
is not evidence that all batches passed.

Observed-data coverage does not replace synthetic boundary cases, particularly
for classes absent in the source, such as zero quantities here. New prices or
input combinations can still escape admission. Exact contracts can prevent wrong
returns on executed calls while allowing those calls to trap, an availability
failure. Never relabel a trapping valid request as a successful business result.

The final task uses the exact protected contract and all development pairs.
The weak-contract bundles are experimental controls, not recommended baselines.
Evidence and limits are in
`docs/agent-evidence/2026-09-08-guard-calibration/REPORT.md`.
