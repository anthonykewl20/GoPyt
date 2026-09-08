"""docs/fmt.md: "The formatter preserves their text and attachment."

A formatter that silently drops comments loses the author's intent, so every
attachment site the grammar allows is pinned here, including idempotence.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from gopyt.fmt import fmt_module
from gopyt.parser import parse_module
from gopyt.testing import run_cli, write_pkg

RICH = """module demo

// why this module exists
// second line of the reason
use core.log { write } // the only effect we need

// a payment, as the gateway sees it
type Payment {
    // minor units
    amount: i64
    currency: str // ISO 4217
}

// the states a payment moves through
enum Status {
    // nothing has happened yet
    Pending
    Paid {
        amount: i64 // what actually cleared
    }
}

// the entry point
task charge(status: Status) -> unit
    effects { log }
{
    // pick the amount out of the state
    total = match status {
        // nothing to charge
        Status.Pending -> 0
        Status.Paid { amount } -> amount // the cleared value
    }
    core.log.write("charged") // audit trail
    return unit
    // nothing follows
}

// end of file
"""


class Comments(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def texts(self, src: str) -> list[str]:
        return [line.strip() for line in src.split("\n") if line.strip().startswith("//")]

    def test_formatting_keeps_every_comment(self) -> None:
        module = parse_module(RICH, "impl/demo.gopyt", "impl")
        out = fmt_module(module)
        self.assertEqual(self.texts(RICH), self.texts(out))

    def test_formatting_is_idempotent_with_comments(self) -> None:
        once = fmt_module(parse_module(RICH, "impl/demo.gopyt", "impl"))
        twice = fmt_module(parse_module(once, "impl/demo.gopyt", "impl"))
        self.assertEqual(once, twice)

    def test_a_commented_package_checks(self) -> None:
        """Types live in spec/ only (elaborator.md); comments live in both."""
        spec = (
            "module demo\n\n"
            "// the states a payment moves through\n"
            "enum Status {\n"
            "    // nothing has happened yet\n"
            "    Pending\n"
            "    Paid {\n        amount: i64 // what actually cleared\n    }\n}\n\n"
            "// the entry point\n"
            "task charge(status: Status) -> unit\n    effects { log }\n"
        )
        impl = (
            "module demo\n\n"
            "use core.log { write } // the only effect we need\n\n"
            "// the entry point\n"
            "task charge(status: Status) -> unit\n    effects { log }\n{\n"
            "    // pick the amount out of the state\n"
            "    total = match status {\n"
            "        // nothing to charge\n"
            "        Status.Pending -> 0\n"
            "        Status.Paid { amount } -> amount // the cleared value\n"
            "    }\n"
            '    core.log.write(core.str.from_i64(total)) // audit trail\n'
            "    return unit\n"
            "    // nothing follows\n"
            "}\n"
        )
        impl = impl.replace(
            "use core.log { write } // the only effect we need",
            "use core.log { write } // the only effect we need\nuse core.str { from_i64 }",
        )
        write_pkg(self.td, {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl})
        code, out = run_cli(self.td, "check")
        self.assertEqual(code, 0, out)
        kept = Path(self.td, "impl/demo.gopyt").read_text(encoding="utf-8")
        self.assertEqual(self.texts(impl), self.texts(kept))

    def test_fmt_does_not_delete_comments_on_disk(self) -> None:
        write_pkg(
            self.td,
            {
                "spec/demo.gopyt": "module demo\n\n// note\nfn one() -> i64\n",
                "impl/demo.gopyt": "module demo\n\nfn one() -> i64\n{\n    // why\n    return 1\n}\n",
            },
            lock=False,
        )
        run_cli(self.td, "fmt")
        self.assertIn("// note", Path(self.td, "spec/demo.gopyt").read_text())
        self.assertIn("// why", Path(self.td, "impl/demo.gopyt").read_text())

    def test_a_comment_is_never_a_contract(self) -> None:
        """fmt.md: comments never become a contract or an `open`."""
        src = "module demo\n\n// requires value > 0\nfn one(value: i64) -> i64\n"
        module = parse_module(src, "spec/demo.gopyt", "spec")
        self.assertEqual(module.items[0].sig.contracts, [])
        self.assertIn("// requires value > 0", fmt_module(module))

    def test_comment_only_module_keeps_its_text(self) -> None:
        src = "module demo\n\n// nothing here yet\n"
        module = parse_module(src, "spec/demo.gopyt", "spec")
        self.assertIn("// nothing here yet", fmt_module(module))

    def test_trailing_whitespace_is_removed(self) -> None:
        src = "module demo\n\n// padded   \nfn one() -> i64\n"
        out = fmt_module(parse_module(src, "spec/demo.gopyt", "spec"))
        self.assertIn("// padded\n", out)
        self.assertNotIn("  \n", out)


if __name__ == "__main__":
    unittest.main()
