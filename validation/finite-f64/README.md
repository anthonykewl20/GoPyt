# Finite f64 boundary validation

Baseline reproduction: main `689eda92d47a2b846f7ec4cffefda0b7e5a4169a` accepts
400-digit decimal source as infinity (`before.json`). The regression now expects
E118 and verifies that a prior output artifact remains unchanged.

Runtime fingerprint: `aafe86cef4d3a53a3ef4fe505a3322f2913085ba291db4e68403fafc4f1af1cb`.
Focused seven tests passed on Python 3.11.16 and 3.14.7. The clean full 3.14.7
run passes all 776 tests. The first run had 22 Guard setup errors and two dependent
diagnostic-audit failures: refreshing example locks left an unheld generated
`.gopyt-transaction.lock` inside the policy source tree. Removing that generated
file corrected setup; Guard checks and test expectations were unchanged. Both
full logs are retained.

The 17 Guard calibration tests, stdlib parity (20 modules), contracts demo,
installed wheel smoke and old-format upgrade/rollback pass. Two isolated builds
at epoch 1788998400 match SHA256
`65e1c77d720d8cbb2a3a58601181112a405ac656ee2e96733c93f26335bbb35b`.

See the normative amendment and `gopyt/test_finite_f64.py` for the precise domain,
oracles and limits. This does not complete architecture issue #17 or qualify
money/time APIs, real data or production performance.
