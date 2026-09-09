# Exact money validation

Runtime source fingerprint:
`c6077e515ab9be931d24f7ae9b28e83eaba5a16327a7c439fb840da1a738c745`.
Python 3.14.7 full suite: 783 tests pass in 331.825 seconds. Seven compiled money
oracle tests also pass on Python 3.11.16. The independent Decimal oracle covers
signed rounding, halfway cases, negative denominators, exact-mode rejection,
rescaling, arithmetic, parsing and boundary/malformed inputs. Runtime arithmetic
uses integers only. No runtime dependency was added.

All 17 Guard calibration tests, stdlib parity (21 modules), contracts demo,
installed wheel smoke and upgrade/rollback pass. Two isolated wheel builds at
epoch 1788998400 match SHA256
`f03236631c1905391161f4dff5e3d4fd42f5103634670fb6066d3645fdd5974b`.

## Full historical price/quantity replay

Run `python tools/money_retail_probe.py '/path/to/Online Retail.xlsx' --output /tmp/money-new.json`.
The tool requires the existing reviewed workbook SHA256 and an unused output path.
The report records the frozen runtime/tool/workbook identities, declared rounding
policy and acceptance requirement before computation, and checks identities again
before passing. See the source citation/license in the report and retail example.
No customer identifiers or descriptions are emitted.

All 541,909 rows pass independent Decimal line and cumulative-total comparisons:
10,624 negative quantities, 2 negative prices, 2,515 zero prices and 51,174 unit
prices needing rounding. The 1,630 distinct raw price cells are parsed through
compiled GoPyt functions once each; every row's quantity multiplication and total
addition run through compiled GoPyt. Aggregate coefficient is 974,774,793 at scale
2 GBP. Zero mismatches. This is an explicit test policy (unit price HalfEven to
scale 2, then signed quantity exactly), not a claim about actual amounts charged.
Workbook exponent notation is expanded exactly before API admission; the money
parser itself intentionally rejects exponents. No source decimal precision is
silently discarded during that expansion.

This dataset produced no difference between unit-first and line-first rounding;
synthetic Decimal cases supply the halfway and signed direction tests. No clock,
timezone, payment, accounting correctness or production throughput claim follows.

The initial probe failed after 95 rows because its host price cache was not a VM
root. The corrected probe pins retained cache and aggregate values with the
existing heap API. Both reports are retained; the runtime was not weakened to
make the probe pass. Caller-owned retained language values need explicit roots.

Architecture #17 remains open for typed time and its remaining qualification.
