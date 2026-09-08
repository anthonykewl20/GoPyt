"""Every GOPYT_E* in docs/diagnostics.md is either exercised or declared untested.

This runs the rest of the suite in-process with `CompileError` instrumented and
compares the codes actually raised against the document's table. A code that no
test can reach yet must be listed in UNTESTED with the reason, so the gap is
visible in review instead of implied.
"""

from __future__ import annotations

import io
import re
import unittest
from pathlib import Path

from gopyt import diag as diag_module

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "diagnostics.md"

# code -> why no test raises it here.
UNTESTED = {
    15: "E015 use_star: the lexer rejects `from`/`import` as illegal keywords "
        "(E017) before a wildcard import can be spelled.",
    34: "E034 spec_unresolved: an `unresolved` statement can only sit in a body, "
        "and a body in spec/ is E031, so the code is structurally unreachable.",
    27: "E027 coerce: no implicit conversion exists to attempt, so every case "
        "surfaces as E021 instead.",
    63: "E063 open_id: the grammar admits only `open snake`, so a malformed id "
        "is a parse error.",
    64: "E064 english_contract: prose in a contract is a parse error (E011).",
    90: "E090 http_verb: an unknown verb is not a token, so the parser reports "
        "it before the http checker sees it.",
    116: "E116 evolve_hot: the VM has no opcode or API that could patch loaded "
         "bytecode, so nothing can raise it.",
}

SKIP_MODULES = {"gopyt.test_diagnostics"}


def documented_codes() -> set[int]:
    text = DOC.read_text(encoding="utf-8")
    return {int(m) for m in re.findall(r"^\| E(\d{3}) \|", text, flags=re.M)}


def raised_codes() -> set[int]:
    seen: set[int] = set()
    original = diag_module.Diag.__init__

    def record(self, *a, **kw):  # noqa: ANN001,ANN002
        original(self, *a, **kw)
        seen.add(self.code)

    # Every diagnostic is a Diag, whether it travels as a CompileError or is
    # written straight to stdout by the CLI (E101 traps, E022 arity).
    diag_module.Diag.__init__ = record
    try:
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        for path in sorted(ROOT.glob("gopyt/test_*.py")):
            name = f"gopyt.{path.stem}"
            if name in SKIP_MODULES:
                continue
            suite.addTests(loader.loadTestsFromName(name))
        runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0)
        result = runner.run(suite)
    finally:
        diag_module.Diag.__init__ = original
    if not result.wasSuccessful():
        raise AssertionError("the rest of the suite must pass before auditing codes")
    return seen


class Coverage(unittest.TestCase):
    def test_every_documented_code_is_raised_or_declared(self) -> None:
        documented = documented_codes()
        self.assertGreater(len(documented), 60)
        seen = raised_codes()
        missing = sorted(documented - seen - set(UNTESTED))
        self.assertEqual(missing, [], f"no test raises: {missing}")

    def test_untested_list_stays_honest(self) -> None:
        """A code that a test does reach must not sit in UNTESTED."""
        seen = raised_codes()
        stale = sorted(set(UNTESTED) & seen)
        self.assertEqual(stale, [], f"UNTESTED lists codes that are raised: {stale}")

    def test_untested_codes_are_documented(self) -> None:
        documented = documented_codes()
        self.assertEqual(sorted(set(UNTESTED) - documented), [])
        for code, reason in UNTESTED.items():
            self.assertTrue(reason.startswith(f"E{code:03d}"), code)


if __name__ == "__main__":
    unittest.main()
