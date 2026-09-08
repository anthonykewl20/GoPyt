"""P2 pass criterion (docs/validation.md), mechanized.

`tools/repair_loop.py` may apply only the repair bytes a diagnostic carries.
It must reach `check` exit 0 on a repairable package and must stop, rather than
guess, when a diagnostic has no exact replacement text.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from gopyt.manifest import TOOLCHAIN
from gopyt.testing import run_cli, write_pkg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import repair_loop  # noqa: E402


class RepairLoop(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def test_missing_use_and_lock_are_repaired(self) -> None:
        """C002 shape: no `use`, no lock. Both repairs are exact text."""
        write_pkg(
            self.td,
            {
                "spec/orders.gopyt": "module orders\n\ntask ping() -> unit\n    effects { log }\n",
                "impl/orders.gopyt": (
                    "module orders\n\ntask ping() -> unit\n    effects { log }\n{\n"
                    '    return core.log.write("x")\n}\n'
                ),
            },
            lock=False,
        )
        status, log = repair_loop.run(self.td)
        self.assertEqual(status, 0, log)
        self.assertLessEqual(len(log), 20)
        self.assertIn("use core.log { write }", Path(self.td, "impl/orders.gopyt").read_text())
        self.assertEqual(run_cli(self.td, "check")[0], 0)

    def test_widening_an_existing_allowlist(self) -> None:
        write_pkg(
            self.td,
            {
                "spec/orders.gopyt": "module orders\n\ntask ping() -> unit\n    effects { log }\n",
                "impl/orders.gopyt": (
                    "module orders\n\nuse core.str { concat }\n\n"
                    "task ping() -> unit\n    effects { log }\n{\n"
                    '    line = core.str.concat("a", "b")\n'
                    "    return core.log.write(line)\n}\n"
                ),
            },
            lock=False,
        )
        status, log = repair_loop.run(self.td)
        self.assertEqual(status, 0, log)
        self.assertEqual(run_cli(self.td, "check")[0], 0)

    def test_int_stops_without_inventing_a_dialect(self) -> None:
        """C005: E020 has no exact replacement text, so the loop must stop."""
        write_pkg(
            self.td,
            {"spec/math.gopyt": "module math\n\nfn add(value: int) -> i64\n"},
            lock=False,
        )
        before = Path(self.td, "spec/math.gopyt").read_text()
        status, log = repair_loop.run(self.td)
        self.assertEqual(status, 1)
        self.assertIn("GOPYT_E020: no applicable repair", log)
        self.assertEqual(Path(self.td, "spec/math.gopyt").read_text(), before)

    def test_open_hole_stops(self) -> None:
        """E065 is a hole a human or agent must fill; no repair bytes."""
        spec = "module math\n\nfn add(left: i64, right: i64) -> i64\n    requires open needs_gateway\n"
        impl = (
            "module math\n\nfn add(left: i64, right: i64) -> i64\n"
            "    requires open needs_gateway\n{\n    return left + right\n}\n"
        )
        write_pkg(self.td, {"spec/math.gopyt": spec, "impl/math.gopyt": impl}, lock=False)
        status, log = repair_loop.run(self.td)
        self.assertEqual(status, 1)
        self.assertIn("GOPYT_E065: no applicable repair", log)

    def test_stale_lock_alone_is_repaired(self) -> None:
        write_pkg(
            self.td,
            {
                "spec/math.gopyt": "module math\n\nfn one() -> i64\n",
                "impl/math.gopyt": "module math\n\nfn one() -> i64\n{\n    return 1\n}\n",
            },
            lock=False,
        )
        Path(self.td, "gopyt.lock").write_text(f'toolchain = "{TOOLCHAIN}"\n', encoding="utf-8")
        status, log = repair_loop.run(self.td)
        self.assertEqual(status, 0, log)
        self.assertIn("GOPYT_E041: wrote gopyt.lock", log)

    def test_the_missing_impl_repair_is_a_file_that_parses(self) -> None:
        """E033's repair is the E-D2 stub: it reaches E030, not E013 or E020."""
        write_pkg(
            self.td,
            {
                "spec/orders.gopyt": (
                    "module orders\n\nuse core.status { NotFound }\n\n"
                    "type Order {\n    id: i64\n}\n\n"
                    "task load(id: i64) -> Order | NotFound\n    effects { database.read }\n"
                )
            },
            lock=False,
        )
        status, log = repair_loop.run(self.td)
        self.assertEqual(status, 1)
        self.assertIn("GOPYT_E030: no applicable repair", log)
        written = Path(self.td, "impl/orders.gopyt").read_text(encoding="utf-8")
        self.assertIn("use core.status { NotFound }", written)
        self.assertIn("unresolved load", written)

    def test_several_errors_converge(self) -> None:
        """Unformatted, missing use, missing impl, and no lock, all at once."""
        write_pkg(
            self.td,
            {
                "spec/orders.gopyt": (
                    "module orders\n\n\n\ntask ping() -> unit\n    effects { log }\n"
                ),
            },
            lock=False,
        )
        status, log = repair_loop.run(self.td)
        # The stub still holds an `unresolved` hole, which only an agent fills.
        self.assertEqual(status, 1)
        self.assertIn("GOPYT_E030: no applicable repair", log)
        self.assertLessEqual(len(log), 20)
        impl = Path(self.td, "impl/orders.gopyt").read_text(encoding="utf-8")
        self.assertIn("unresolved ping", impl)
        # Filling the hole by hand is all that is left.
        Path(self.td, "impl/orders.gopyt").write_text(
            "module orders\n\nuse core.log { write }\n\n"
            "task ping() -> unit\n    effects { log }\n{\n"
            '    return core.log.write("x")\n}\n',
            encoding="utf-8",
        )
        status, log = repair_loop.run(self.td)
        self.assertEqual(status, 0, log)

    def test_a_repair_only_touches_the_file_it_names(self) -> None:
        files = {
            "spec/one.gopyt": "module one\n\nfn alpha() -> i64\n",
            "impl/one.gopyt": "module one\n\nfn alpha() -> i64\n{\n    return 1\n}\n",
            "spec/two.gopyt": "module two\n\ntask beta() -> unit\n    effects { log }\n",
            "impl/two.gopyt": (
                "module two\n\ntask beta() -> unit\n    effects { log }\n{\n"
                '    return core.log.write("x")\n}\n'
            ),
        }
        write_pkg(self.td, files, lock=False)
        untouched = Path(self.td, "impl/one.gopyt").read_text(encoding="utf-8")
        status, log = repair_loop.run(self.td)
        self.assertEqual(status, 0, log)
        self.assertEqual(Path(self.td, "impl/one.gopyt").read_text(encoding="utf-8"), untouched)
        self.assertIn("use core.log { write }", Path(self.td, "impl/two.gopyt").read_text())

    def test_runs_as_a_script(self) -> None:
        write_pkg(
            self.td,
            {
                "spec/math.gopyt": "module math\n\nfn one() -> i64\n",
                "impl/math.gopyt": "module math\n\nfn one() -> i64\n{\n    return 1\n}\n",
            },
            lock=False,
        )
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "repair_loop.py"), self.td],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("check clean", proc.stdout)


class AgainstTheFixtures(unittest.TestCase):
    """Every conformance package, through the loop, in a temporary copy."""

    def test_each_fixture_converges_or_stops_at_its_own_code(self) -> None:
        from gopyt.test_fixtures import cases, parse_expect

        found = [c for c in cases() if (c / "gopyt.toml").is_file()]
        if not found:
            self.skipTest("no conformance/ fixtures present")
        problems = []
        for case in found:
            expect, meta = parse_expect(case / "expect")
            if meta.get("phase", "check") != "check":
                continue
            work = Path(tempfile.mkdtemp()) / "pkg"
            shutil.copytree(case, work)
            try:
                status, log = repair_loop.run(str(work))
            except Exception as exc:  # noqa: BLE001 - the loop must not crash
                problems.append(f"{case.name}: {type(exc).__name__}: {exc}")
                continue
            finally:
                shutil.rmtree(work.parent, ignore_errors=True)
            if len(log) > 20:
                problems.append(f"{case.name}: {len(log)} cycles")
            if status == 0:
                # Every error the fixture had carried an exact repair.
                continue
            last = log[-1]
            if expect != "ok" and last.startswith(f"GOPYT_{expect}"):
                continue
            # docs/conformance.md C038 admits "E074 or E110".
            if {expect, last.split(":")[0].replace("GOPYT_", "")} == {"E074", "E110"}:
                continue
            problems.append(f"{case.name}: expect {expect}, loop ended with {last!r}")
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
