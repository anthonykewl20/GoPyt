"""Performance smoke: the compiler and the VM finish, and observe stays O(1).

Numbers are recorded in PROGRESS.md. The assertions are deliberately loose --
this guards against an accidental quadratic, not against a slow machine.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from gopyt.cli import build, make_vm
from gopyt.testing import run_cli

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "auth"
BURST = 10_000


def stage(iterations: int) -> str:
    work = Path(tempfile.mkdtemp()) / "auth"
    shutil.copytree(EXAMPLE, work)
    impl = work / "impl" / "auth.gopyt"
    impl.write_text(
        re.sub(r"core\.list\.range\(0, \d+\)", f"core.list.range(0, {iterations})",
               impl.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    from gopyt import evolve

    evolve._write_lock(str(work))
    return str(work)


class Smoke(unittest.TestCase):
    def setUp(self) -> None:
        self.root = stage(BURST)

    def tearDown(self) -> None:
        shutil.rmtree(Path(self.root).parent, ignore_errors=True)

    def test_check_and_a_ten_thousand_denial_burst(self) -> None:
        start = time.monotonic()
        code, out = run_cli(self.root, "check")
        check_ms = (time.monotonic() - start) * 1000
        self.assertEqual(code, 0, out)

        prog, art, fn_ids = build(self.root)
        vm = make_vm(self.root, prog, art, fn_ids)
        cells = vm.observe.memory_cells()
        start = time.monotonic()
        alarm = vm.call(fn_ids["auth.burst"], [])
        run_ms = (time.monotonic() - start) * 1000
        self.assertTrue(alarm)
        self.assertGreaterEqual(vm.observe.events, BURST // 2)
        self.assertEqual(vm.observe.memory_cells(), cells)
        self.assertLessEqual(len(vm.observe.reservoir.items), vm.observe.reservoir.k)
        print(f"\ncheck {check_ms:.0f} ms, {BURST} logins {run_ms:.0f} ms")
        self.assertLess(check_ms, 30_000)
        self.assertLess(run_ms, 180_000)

    def test_observe_memory_is_flat_across_sizes(self) -> None:
        """Ten times the events must not mean more cells."""
        from gopyt.observe import Observe

        small = Observe(reservoir_k=32)
        large = Observe(reservoir_k=32)
        for _ in range(1_000):
            small.event("denied", True, 1.0)
        for _ in range(100_000):
            large.event("denied", True, 1.0)
        self.assertEqual(small.memory_cells(), large.memory_cells())
        self.assertEqual(len(small.reservoir.items), len(large.reservoir.items))


class ArtifactOrder(unittest.TestCase):
    """implementer.md 10: ids follow the canonical sort, not emit order."""

    def test_ids_are_sorted_by_symbol_and_type_arguments(self) -> None:
        from gopyt.gobyte import decode
        from gopyt.testing import run_cli, write_pkg

        td = tempfile.mkdtemp()
        try:
            spec = (
                "module demo\n\ntype Zebra {\n    tag: str\n}\n\n"
                "type Apple {\n    tag: str\n}\n\n"
                "fn identity[T](value: T) -> T\n\n"
                "fn zebra() -> Zebra\n\nfn apple() -> Apple\n\n"
                "fn mixed(left: i64, right: str) -> str\n"
            )
            impl = (
                "module demo\n\nfn identity[T](value: T) -> T\n{\n    return value\n}\n\n"
                'fn zebra() -> Zebra\n{\n    return Zebra { tag: "z" }\n}\n\n'
                'fn apple() -> Apple\n{\n    return Apple { tag: "a" }\n}\n\n'
                "fn mixed(left: i64, right: str) -> str\n{\n"
                "    first = identity(left)\n    return identity(right)\n}\n"
            )
            write_pkg(td, {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl}, fmt=True)
            self.assertEqual(run_cli(td, "check")[0], 0)
            art = decode(Path(td, "build/out.gobyte").read_bytes())
            type_names = [art.const_str(t.name) for t in art.types]
            self.assertEqual(type_names, sorted(type_names, key=lambda n: n.encode("utf-8")))
            self.assertLess(type_names.index("demo.Apple"), type_names.index("demo.Zebra"))
            fn_names = [art.const_str(f.name) for f in art.funcs]
            self.assertEqual(fn_names, sorted(fn_names, key=lambda n: n.encode("utf-8")))
            self.assertIn("demo.identity[i64]", fn_names)
            self.assertIn("demo.identity[str]", fn_names)
        finally:
            shutil.rmtree(td, ignore_errors=True)


class ManyModules(unittest.TestCase):
    """A package with many modules stays roughly linear to check."""

    def test_fifty_modules(self) -> None:
        from gopyt.testing import run_cli, write_pkg

        td = tempfile.mkdtemp()
        try:
            files = {}
            for i in range(50):
                files[f"spec/mod{i}.gopyt"] = (
                    f"module mod{i}\n\nfn value{i}() -> i64\n"
                )
                files[f"impl/mod{i}.gopyt"] = (
                    f"module mod{i}\n\nfn value{i}() -> i64\n{{\n    return {i}\n}}\n"
                )
            write_pkg(td, files, fmt=True)
            start = time.monotonic()
            code, out = run_cli(td, "check")
            elapsed = (time.monotonic() - start) * 1000
            self.assertEqual(code, 0, out)
            print(f"\n50 modules checked in {elapsed:.0f} ms")
            self.assertLess(elapsed, 30_000)
        finally:
            shutil.rmtree(td, ignore_errors=True)


class Determinism(unittest.TestCase):
    """RP-002: the same sources produce the same bytes, in any process."""

    def test_the_same_broken_package_reports_the_same_diagnostic(self) -> None:
        """docs/diagnostics.md: first error only, chosen deterministically."""
        import os
        import subprocess
        import sys

        from gopyt.testing import write_pkg

        root = Path(__file__).resolve().parents[1]
        work = tempfile.mkdtemp()
        files = {}
        for i in range(6):
            files[f"spec/mod{i}.gopyt"] = (
                f"module mod{i}\n\ntask ping{i}() -> unit\n    effects {{ log, time }}\n"
            )
            files[f"impl/mod{i}.gopyt"] = (
                f"module mod{i}\n\nuse core.log {{ write }}\n\n"
                f"task ping{i}() -> unit\n    effects {{ log, time }}\n{{\n"
                f'    return core.log.write("x")\n}}\n'
            )
        write_pkg(work, files, lock=False)
        seen = set()
        for seed in ("0", "1", "2", "3"):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(root))
            proc = subprocess.run(
                [sys.executable, "-m", "gopyt", "check"],
                cwd=work,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)
            seen.add(proc.stdout)
        self.assertEqual(len(seen), 1, seen)
        self.assertIn("GOPYT_E051", seen.pop())
        shutil.rmtree(work, ignore_errors=True)

    def test_artifact_is_identical_under_different_hash_seeds(self) -> None:
        import os
        import subprocess
        import sys

        root = Path(__file__).resolve().parents[1]
        work = stage(40)
        digests = []
        for seed in ("0", "1", "12345"):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(root))
            proc = subprocess.run(
                [sys.executable, "-m", "gopyt", "check"],
                cwd=work,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            digests.append(Path(work, "build/out.gobyte").read_bytes())
        self.assertEqual(digests[0], digests[1])
        self.assertEqual(digests[1], digests[2])
        shutil.rmtree(Path(work).parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
