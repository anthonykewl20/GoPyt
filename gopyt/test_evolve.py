"""core.evolve.propose (docs/evolve.md): candidates, gates, and the digest.

Nothing here calls a model or the network. The pass criteria being checked are
evolve.md's: quiet CUSUM answers NoChange, a candidate that fails check is
discarded with the digest unchanged, a candidate may not add `ffi` or drop an
`egress` origin, and the loaded bytecode is never patched.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from gopyt import evolve
from gopyt.cli import build, make_vm
from gopyt.manifest import package_digest
from gopyt.testing import run_cli

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "auth"


def stage() -> str:
    work = Path(tempfile.mkdtemp()) / "auth"
    shutil.copytree(EXAMPLE, work)
    return str(work)


class Pipeline(unittest.TestCase):
    def setUp(self) -> None:
        self.root = stage()

    def tearDown(self) -> None:
        shutil.rmtree(Path(self.root).parent, ignore_errors=True)

    def test_quiet_cusum_is_nochange_and_writes_nothing(self) -> None:
        before = package_digest(self.root)
        out = evolve.propose(self.root, alarm=False, max_candidates=4)
        self.assertEqual(out.kind, "NoChange")
        self.assertFalse(Path(self.root, "evolve").exists())
        self.assertEqual(package_digest(self.root), before)

    def test_alarm_tightens_the_limiter_and_moves_the_digest(self) -> None:
        before = package_digest(self.root)
        out = evolve.propose(self.root, alarm=True, max_candidates=4)
        self.assertEqual(out.kind, "Applied", out.message)
        after = package_digest(self.root)
        self.assertNotEqual(before, after)
        self.assertEqual(out.digest, after)
        impl = Path(self.root, "impl/auth.gopyt").read_text(encoding="utf-8")
        self.assertIn("core.limit.allow(req.user, 2, 3600000)", impl)
        self.assertEqual(
            Path(self.root, "gopyt.lock").read_text(encoding="utf-8").count(after), 1
        )
        # The applied package is still a package the compiler accepts.
        self.assertEqual(run_cli(self.root, "check")[0], 0)
        self.assertEqual(run_cli(self.root, "test")[0], 0)

    def test_candidates_never_add_ffi_or_drop_egress(self) -> None:
        base_egress, base_ffi = evolve._survey(self.root)
        for edits in evolve.candidates(self.root, 4):
            for _rel, text in edits:
                self.assertNotIn("ffi", text)
                self.assertNotIn("egress", text)
        self.assertEqual(base_egress, set())
        self.assertEqual(base_ffi, set())

    def test_a_candidate_that_fails_check_is_discarded(self) -> None:
        """Force every candidate to be illegal; the package must not move."""
        before = package_digest(self.root)
        original = evolve.candidates

        def broken(root: str, limit: int):
            sets = original(root, limit)
            out = []
            for edits in sets:
                out.append([(rel, text + "\ntask broken() -> unit\n    effects { ffi }\n{\n    return unit\n}\n") for rel, text in edits])
            return out

        evolve.candidates = broken
        try:
            out = evolve.propose(self.root, alarm=True, max_candidates=2)
        finally:
            evolve.candidates = original
        self.assertEqual(out.kind, "EvolveError")
        self.assertEqual(package_digest(self.root), before)
        self.assertIn("core.limit.allow(req.user, 4, 3600000)",
                      Path(self.root, "impl/auth.gopyt").read_text(encoding="utf-8"))

    def test_a_package_with_no_limiter_has_nothing_to_change(self) -> None:
        work = Path(tempfile.mkdtemp())
        from gopyt.testing import write_pkg

        write_pkg(str(work), {
            "spec/demo.gopyt": "module demo\n\nfn ok() -> i64\n",
            "impl/demo.gopyt": "module demo\n\nfn ok() -> i64\n{\n    return 1\n}\n",
        })
        try:
            self.assertEqual(evolve.propose(str(work), True, 4).kind, "NoChange")
        finally:
            shutil.rmtree(work, ignore_errors=True)


class ThroughTheVm(unittest.TestCase):
    """The full loop under `gopyt run`, with no model and no network."""

    AGENT = (
        "\nagent Warden\n"
        "    effects { database.read, filesystem.read, filesystem.write, time, log, model, observe }\n"
        "    tasks { burst, harden }\n"
        "    evolve {\n        max 2\n        timeout_ms 30000\n        reservoir 32\n    }\n"
        "\ntask harden() -> Applied | NoChange | EvolveError\n"
        "    effects { database.read, filesystem.read, filesystem.write, log, model, observe, time }\n"
    )
    IMPL = (
        "\ntask harden() -> Applied | NoChange | EvolveError\n"
        "    effects { database.read, filesystem.read, filesystem.write, log, model, observe, time }\n"
        "{\n"
        "    hot = burst()\n"
        "    return core.evolve.propose()\n"
        "}\n"
    )

    def setUp(self) -> None:
        self.root = stage()
        spec = Path(self.root, "spec/auth.gopyt")
        text = spec.read_text(encoding="utf-8").replace(
            "use core.status { DbError, Throttled }",
            "use core.evolve { Applied, EvolveError, NoChange }\nuse core.status { DbError, Throttled }",
        )
        spec.write_text(text + self.AGENT, encoding="utf-8")
        impl = Path(self.root, "impl/auth.gopyt")
        text = impl.read_text(encoding="utf-8").replace(
            "use core.limit { allow }",
            "use core.evolve { Applied, EvolveError, NoChange, propose }\nuse core.limit { allow }",
        )
        impl.write_text(text + self.IMPL, encoding="utf-8")
        from gopyt.cli import cmd_fmt

        cmd_fmt(self.root)
        evolve._write_lock(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(Path(self.root).parent, ignore_errors=True)

    def test_check_accepts_the_evolve_agent(self) -> None:
        code, out = run_cli(self.root, "check")
        self.assertEqual(code, 0, out)

    def test_burst_then_propose_applies_a_checked_candidate(self) -> None:
        self.assertEqual(run_cli(self.root, "check")[0], 0)
        artifact = Path(self.root, "build/out.gobyte").read_bytes()
        before = package_digest(self.root)
        code, out = run_cli(self.root, "run", "auth.harden")
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith('{"core.evolve.Applied"'), out)
        # Sources and lock moved; the loaded artifact on disk did not (E116).
        self.assertNotEqual(package_digest(self.root), before)
        self.assertEqual(Path(self.root, "build/out.gobyte").read_bytes(), artifact)
        self.assertIn(
            "core.limit.allow(req.user, 2, 3600000)",
            Path(self.root, "impl/auth.gopyt").read_text(encoding="utf-8"),
        )
        self.assertTrue(Path(self.root, "evolve").is_dir())

    def test_propose_without_an_alarm_is_nochange(self) -> None:
        """Same package, no burst: the model is never consulted."""
        import re

        # Drop the burst call, so harden holds exactly propose's effect set.
        narrow = "    effects { filesystem.read, filesystem.write, time, log, model, observe }\n"
        for rel in ("spec/auth.gopyt", "impl/auth.gopyt"):
            path = Path(self.root, rel)
            text = path.read_text(encoding="utf-8")
            head, sep, tail = text.rpartition("task harden()")
            tail = re.sub(r"    effects \{[^}]*\}\n", narrow, tail, count=1)
            tail = tail.replace("    hot = burst()\n", "")
            path.write_text(head + sep + tail, encoding="utf-8")
        from gopyt.cli import cmd_fmt

        cmd_fmt(self.root)
        evolve._write_lock(self.root)
        before = package_digest(self.root)
        code, out = run_cli(self.root, "run", "auth.harden")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip(), '{"core.evolve.NoChange":{}}')
        self.assertEqual(package_digest(self.root), before)

    def test_propose_outside_an_evolve_module_is_e115(self) -> None:
        work = Path(tempfile.mkdtemp())
        from gopyt.testing import write_pkg

        spec = (
            "module demo\n\nuse core.evolve { Applied, EvolveError, NoChange }\n\n"
            "task tick() -> Applied | NoChange | EvolveError\n"
            "    effects { filesystem.read, filesystem.write, log, model, observe, time }\n"
        )
        impl = (
            "module demo\n\nuse core.evolve { Applied, EvolveError, NoChange, propose }\n\n"
            "task tick() -> Applied | NoChange | EvolveError\n"
            "    effects { filesystem.read, filesystem.write, log, model, observe, time }\n{\n"
            "    return core.evolve.propose()\n}\n"
        )
        write_pkg(str(work), {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl})
        try:
            code, out = run_cli(str(work), "check")
            self.assertEqual(code, 1)
            self.assertIn("GOPYT_E115", out)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_the_agent_sizes_the_reservoir(self) -> None:
        """S32: `evolve { reservoir K }` is the Vitter bound the VM uses."""
        prog, art, fn_ids = build(self.root)
        vm = make_vm(self.root, prog, art, fn_ids)
        self.assertEqual(vm.observe.reservoir.k, 32)
        spec = Path(self.root, "spec/auth.gopyt")
        spec.write_text(
            spec.read_text(encoding="utf-8").replace("reservoir 32", "reservoir 8"),
            encoding="utf-8",
        )
        evolve._write_lock(self.root)
        prog, art, fn_ids = build(self.root)
        vm = make_vm(self.root, prog, art, fn_ids)
        self.assertEqual(vm.observe.reservoir.k, 8)
        vm.call(fn_ids["auth.burst"], [])
        self.assertLessEqual(len(vm.observe.reservoir.items), 8)

    def test_a_second_propose_inside_the_cooldown_is_nochange(self) -> None:
        """hardening.md: the cooldown is the evolve block's timeout_ms."""
        prog, art, fn_ids = build(self.root)
        vm = make_vm(self.root, prog, art, fn_ids)
        vm.observe.cusum.alarm = True
        vm.module_stack.append("auth")
        try:
            first = vm.call(fn_ids["auth.harden"], [])
            self.assertEqual(vm.type_name(first.type_id), "core.evolve.Applied")
            second = vm.call(fn_ids["auth.harden"], [])
            self.assertEqual(vm.type_name(second.type_id), "core.evolve.NoChange")
        finally:
            vm.module_stack.pop()

    def test_in_flight_propose_is_refused(self) -> None:
        prog, art, fn_ids = build(self.root)
        vm = make_vm(self.root, prog, art, fn_ids)
        vm.observe.cusum.alarm = True
        vm.evolve_in_flight = True
        result = vm.call(fn_ids["auth.harden"], [])
        self.assertEqual(vm.type_name(result.type_id), "core.evolve.EvolveError")
        self.assertEqual(result.fields[0], "in flight")


if __name__ == "__main__":
    unittest.main()
