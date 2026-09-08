"""The four commands and their exit codes (docs/spec.md S18, implementer.md 9)."""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from gopyt.cli import main
from gopyt.testing import run_cli, write_pkg

PURE = {
    "spec/demo.gopyt": "module demo\n\nfn one() -> i64\n\ntask main() -> i64\n    effects { log }\n",
    "impl/demo.gopyt": (
        "module demo\n\nuse core.log { write }\n\nfn one() -> i64\n{\n    return 1\n}\n\n"
        "task main() -> i64\n    effects { log }\n{\n"
        '    core.log.write("x")\n    return one()\n}\n'
    ),
}


def stderr_of(argv: list[str], cwd: str) -> tuple[int, str]:
    here = os.getcwd()
    buf = io.StringIO()
    os.chdir(cwd)
    try:
        with redirect_stderr(buf):
            code = main(argv)
    finally:
        os.chdir(here)
    return code, buf.getvalue()


class Commands(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()
        write_pkg(self.td, PURE)

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def test_no_command(self) -> None:
        code, err = stderr_of([], self.td)
        self.assertEqual(code, 1)
        self.assertEqual(err.count("\n"), 1)

    def test_unknown_command(self) -> None:
        code, err = stderr_of(["compile"], self.td)
        self.assertEqual(code, 1)
        self.assertEqual(err.count("\n"), 1)

    def test_check_takes_no_argument(self) -> None:
        code, err = stderr_of(["check", "extra"], self.td)
        self.assertEqual(code, 1)
        self.assertTrue(err)

    def test_run_needs_exactly_one_target(self) -> None:
        self.assertEqual(stderr_of(["run"], self.td)[0], 1)
        self.assertEqual(stderr_of(["run", "a", "b"], self.td)[0], 1)

    def test_run_of_a_task_prints_json(self) -> None:
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "1\n")

    def test_run_of_a_fn_is_e074(self) -> None:
        """implementer.md 9: run targets a task or workflow."""
        code, out = run_cli(self.td, "run", "demo.one")
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E074", out)

    def test_run_of_an_unknown_target_is_e074(self) -> None:
        code, out = run_cli(self.td, "run", "demo.missing")
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E074", out)

    def test_run_of_a_task_with_arguments_is_e022(self) -> None:
        write_pkg(
            self.td,
            {
                "spec/demo.gopyt": "module demo\n\ntask main(value: i64) -> i64\n    effects { log }\n",
                "impl/demo.gopyt": (
                    "module demo\n\nuse core.log { write }\n\n"
                    "task main(value: i64) -> i64\n    effects { log }\n{\n"
                    '    core.log.write("x")\n    return value\n}\n'
                ),
            },
        )
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E022", out)

    def test_run_prints_nothing_for_unit(self) -> None:
        write_pkg(
            self.td,
            {
                "spec/demo.gopyt": "module demo\n\ntask main() -> unit\n    effects { log }\n",
                "impl/demo.gopyt": (
                    "module demo\n\nuse core.log { write }\n\n"
                    "task main() -> unit\n    effects { log }\n{\n"
                    '    return core.log.write("x")\n}\n'
                ),
            },
        )
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_check_writes_the_artifact(self) -> None:
        self.assertEqual(run_cli(self.td, "check")[0], 0)
        self.assertTrue(Path(self.td, "build/out.gobyte").is_file())

    def test_no_manifest_is_e046(self) -> None:
        empty = tempfile.mkdtemp()
        try:
            code, out = run_cli(empty, "check")
            self.assertEqual(code, 1)
            self.assertIn("GOPYT_E046", out)
        finally:
            shutil.rmtree(empty, ignore_errors=True)


class TestOrdering(unittest.TestCase):
    """implementer.md 9: tests run by file path then test name, UTF-8 order."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def test_tests_run_in_lexicographic_order(self) -> None:
        spec = "module demo\n\nfn ok() -> i64\n"
        impl = "module demo\n\nfn ok() -> i64\n{\n    return 1\n}\n"
        # Each test appends to store.db; the last one reads the trail back.
        def body(tag: str, extra: str = "") -> str:
            return (
                f"test {tag}\n{{\n"
                f'    previous = store.db.get("trail")\n'
                "    trail = match previous {\n"
                f'        str -> core.str.concat(previous, "{tag[0]}")\n'
                f'        NotFound -> "{tag[0]}"\n'
                f'        DbError {{ message }} -> "!"\n'
                "    }\n"
                '    store.db.put("trail", trail)\n'
                f"{extra}"
                "    return unit\n}\n"
            )

        tests = (
            "module test.demo\n\n"
            "use core.status { DbError, NotFound }\n"
            "use core.str { concat }\n"
            "use core.test { assert_eq }\n"
            "use store.db { get, put }\n\n"
            + body("alpha")
            + "\n"
            + body("beta")
            + "\n"
            + body("zulu", '    core.test.assert_eq(trail, "abz")\n')
        )
        write_pkg(
            self.td,
            {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl, "test/demo.gopyt": tests},
            fmt=True,
        )
        code, out = run_cli(self.td, "test")
        self.assertEqual(code, 0, out)

    def test_a_failing_test_exits_two_at_the_first_trap(self) -> None:
        spec = "module demo\n\nfn ok() -> i64\n"
        impl = "module demo\n\nfn ok() -> i64\n{\n    return 1\n}\n"
        tests = (
            "module test.demo\n\nuse core.test { assert_eq }\nuse demo { ok }\n\n"
            "test aaa_fails\n{\n    core.test.assert_eq(demo.ok(), 2)\n    return unit\n}\n\n"
            "test zzz_would_pass\n{\n    core.test.assert_eq(demo.ok(), 1)\n    return unit\n}\n"
        )
        write_pkg(
            self.td,
            {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl, "test/demo.gopyt": tests},
            fmt=True,
        )
        code, out = run_cli(self.td, "test")
        self.assertEqual(code, 2)
        self.assertIn("trap: 13", out)


class NestedModules(unittest.TestCase):
    """S3: the path is the module name, in spec/, impl/, and test/."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def test_nested_spec_impl_and_test_modules(self) -> None:
        files = {
            "spec/billing/gateway.gopyt": (
                "module billing.gateway\n\ntask charge(amount: i64) -> i64\n"
                "    effects { log }\n"
            ),
            "impl/billing/gateway.gopyt": (
                "module billing.gateway\n\nuse core.log { write }\n\n"
                "task charge(amount: i64) -> i64\n    effects { log }\n{\n"
                '    core.log.write("charge")\n    return amount\n}\n'
            ),
            "test/billing/gateway.gopyt": (
                "module test.billing.gateway\n\nuse billing.gateway { charge }\n"
                "use core.test { assert_eq }\n\n"
                "test charges\n{\n"
                "    core.test.assert_eq(billing.gateway.charge(5), 5)\n    return unit\n}\n"
            ),
        }
        write_pkg(self.td, files, fmt=True)
        self.assertEqual(run_cli(self.td, "check")[0], 0)
        self.assertEqual(run_cli(self.td, "test")[0], 0)

    def test_a_module_header_must_match_a_nested_path(self) -> None:
        write_pkg(
            self.td,
            {"spec/billing/gateway.gopyt": "module gateway\n\nfn one() -> i64\n"},
            lock=False,
        )
        from gopyt.testing import diag

        err = diag(self.td)
        self.assertEqual(err.diag.code, 12)
        self.assertEqual(err.diag.repair, "module billing.gateway\n")


if __name__ == "__main__":
    unittest.main()
