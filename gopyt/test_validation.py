"""docs/validation.md P0-P4, each criterion as an executable check.

This file is the honest answer to "what is proven". Where a criterion is only
partly met, the test says so in its name and PROGRESS.md carries the detail.
"""

from __future__ import annotations

import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from gopyt.observe import Observe
from gopyt.testing import diag_code, run_cli, write_pkg

ROOT = Path(__file__).resolve().parents[1]


class P0Observe(unittest.TestCase):
    """Sketches stay O(1) and CUSUM sees a planted shift."""

    def test_reservoir_never_exceeds_k(self) -> None:
        random.seed(1)
        obs = Observe(reservoir_k=8)
        for i in range(10_000):
            obs.event(str(i), True, 1.0)
        self.assertEqual(len(obs.reservoir.items), 8)

    def test_planted_shift_alarms(self) -> None:
        random.seed(2)
        obs = Observe(reservoir_k=32)
        for _ in range(3_000):
            obs.event("ok", False, 10.0)
        for _ in range(400):
            fail = random.random() < 0.15
            obs.event("denied" if fail else "ok", fail, 12.0)
        self.assertTrue(obs.cusum.alarm)

    def test_control_stream_alarm_rate_is_low(self) -> None:
        alarms = 0
        trials = 20
        for t in range(trials):
            random.seed(100 + t)
            obs = Observe(reservoir_k=16)
            for _ in range(3_000):
                fail = random.random() < 0.01
                obs.event("denied" if fail else "ok", fail, 10.0)
            alarms += 1 if obs.cusum.alarm else 0
        self.assertLess(alarms, trials // 4)

    def test_memory_is_o1_in_stream_length(self) -> None:
        obs = Observe(reservoir_k=32)
        before = obs.memory_cells()
        for _ in range(50_000):
            obs.event("denied", True, 1.0)
        self.assertEqual(obs.memory_cells(), before)

    def test_the_prototype_still_passes(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "prototypes" / "observe" / "test_sketches.py")],
            capture_output=True,
            text=True,
            cwd=ROOT / "prototypes" / "observe",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class P1Conformance(unittest.TestCase):
    """Every C-case in docs/conformance.md is executable somewhere."""

    def test_c001_to_c041_are_all_covered(self) -> None:
        doc = (ROOT / "docs" / "conformance.md").read_text(encoding="utf-8")
        ids = set(re.findall(r"\bC(\d{3})\b", doc))
        self.assertGreaterEqual(len(ids), 41)
        suite = (ROOT / "gopyt" / "test_conformance.py").read_text(encoding="utf-8")
        fixtures = {p.name for p in (ROOT / "conformance").glob("C*")}
        missing = []
        for case in sorted(ids):
            tag = f"c{case}"
            if tag in suite.lower() or f"C{case}" in fixtures:
                continue
            missing.append(f"C{case}")
        self.assertEqual(missing, [])

    def test_the_fixture_runner_agrees_with_the_compiler(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "gopyt.test_fixtures"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class P2RepairLoop(unittest.TestCase):
    """check reaches 0 through GOPYT_E* repairs alone, and invents no dialect."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def test_a_broken_tree_reaches_check_zero(self) -> None:
        sys.path.insert(0, str(ROOT / "tools"))
        import repair_loop

        write_pkg(
            self.td,
            {
                "spec/orders.gopyt": "module orders\n\n\ntask ping() -> unit\n    effects { log }\n",
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


class P3Harden(unittest.TestCase):
    """A real burst raises cusum_alarm; propose never widens what may run."""

    def setUp(self) -> None:
        work = Path(tempfile.mkdtemp()) / "auth"
        shutil.copytree(ROOT / "examples" / "auth", work)
        self.root = str(work)

    def tearDown(self) -> None:
        shutil.rmtree(Path(self.root).parent, ignore_errors=True)

    def test_burst_raises_the_alarm(self) -> None:
        code, out = run_cli(self.root, "run", "auth.burst")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "true\n")

    def test_propose_keeps_egress_and_ffi_closed(self) -> None:
        from gopyt import evolve

        before_egress, before_ffi = evolve._survey(self.root)
        outcome = evolve.propose(self.root, alarm=True, max_candidates=4)
        self.assertEqual(outcome.kind, "Applied", outcome.message)
        after_egress, after_ffi = evolve._survey(self.root)
        self.assertEqual(before_egress, after_egress)
        self.assertEqual(before_ffi, after_ffi)
        self.assertEqual(after_ffi, set())

    def test_a_candidate_that_fails_check_leaves_the_digest_alone(self) -> None:
        from gopyt import evolve
        from gopyt.manifest import package_digest

        before = package_digest(self.root)
        original = evolve.candidates
        evolve.candidates = lambda root, limit: [
            [(rel, text + "\ntask broken() -> unit\n    effects { ffi }\n{\n    return unit\n}\n")
             for rel, text in edits]
            for edits in original(root, limit)
        ]
        try:
            self.assertEqual(evolve.propose(self.root, True, 2).kind, "EvolveError")
        finally:
            evolve.candidates = original
        self.assertEqual(package_digest(self.root), before)


class P4Security(unittest.TestCase):
    """The five negative attempts, with the codes docs/validation.md names."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def code(self, spec: str, impl: str | None = None) -> int:
        files = {"spec/demo.gopyt": spec}
        if impl is not None:
            files["impl/demo.gopyt"] = impl
        write_pkg(self.td, files, lock=False)
        return diag_code(self.td)

    def test_eval_is_e110(self) -> None:
        self.assertEqual(
            self.code(
                "module demo\n\nfn one() -> i64\n",
                "module demo\n\nfn one() -> i64\n{\n    return eval(1)\n}\n",
            ),
            110,
        )

    def test_app_ffi_is_e069(self) -> None:
        self.assertEqual(
            self.code("module demo\n\ntask ping() -> unit\n    effects { ffi }\n"), 69
        )

    def test_request_off_the_allowlist_is_e111(self) -> None:
        spec = (
            "module demo\n\nuse core.status { HttpError }\n"
            "use net.http { HttpResponse }\n\n"
            'egress { "https://api.example" }\n\n'
            "task fetch() -> HttpResponse | HttpError\n    effects { network }\n"
        )
        impl = (
            "module demo\n\nuse core.bytes { from_str }\nuse core.status { HttpError }\n"
            "use net.http { HttpMethod, HttpRequest, HttpResponse, request }\n\n"
            "task fetch() -> HttpResponse | HttpError\n    effects { network }\n{\n"
            "    req = HttpRequest {\n        method: HttpMethod.Get\n"
            '        url: "https://elsewhere.example/v1"\n'
            '        body: core.bytes.from_str("")\n    }\n'
            "    return net.http.request(req)\n}\n"
        )
        self.assertEqual(self.code(spec, impl), 111)

    def test_logging_a_secret_is_e112(self) -> None:
        spec = "module demo\n\ntask leak() -> unit\n    effects { log, secret }\n"
        impl = (
            "module demo\n\nuse core.log { write }\nuse core.secret { Secret, get }\n"
            "use core.status { NotFound }\n\n"
            "task leak() -> unit\n    effects { log, secret }\n{\n"
            '    found = core.secret.get("api_token")\n'
            "    return match found {\n"
            "        Secret -> core.log.write(found)\n"
            "        NotFound -> unit\n    }\n}\n"
        )
        self.assertEqual(self.code(spec, impl), 112)

    def test_a_json_extra_field_is_convert_error(self) -> None:
        spec = (
            "module demo\n\nuse core.convert { Json }\nuse core.status { ConvertError }\n\n"
            "type Payment {\n    amount: i64\n}\n\nprovide Json for Payment\n\n"
            "task load() -> Payment | ConvertError\n    effects { log }\n"
        )
        impl = (
            "module demo\n\nuse core.log { write }\nuse core.status { ConvertError }\n"
            "use data.json { decode }\n\n"
            "task load() -> Payment | ConvertError\n    effects { log }\n{\n"
            '    core.log.write("decode")\n'
            '    return data.json.decode[Payment]("{\\"amount\\": 1, \\"extra\\": 2}")\n}\n'
        )
        write_pkg(self.td, {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl})
        code, out = run_cli(self.td, "run", "demo.load")
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith('{"core.status.ConvertError"'), out)


if __name__ == "__main__":
    unittest.main()
