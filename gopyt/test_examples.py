"""examples/auth: the P3 shape from docs/hardening.md, exercised end to end.

This is not the full P3 pass criterion in docs/validation.md (that adds an
`evolve.propose` digest decision); it covers the observe half: a real burst of
refusals makes `report().cusum_alarm` true while the sketches stay bounded.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from gopyt.cli import build, make_vm
from gopyt.testing import run_cli

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "auth"


def stage() -> str:
    work = Path(tempfile.mkdtemp()) / "auth"
    shutil.copytree(EXAMPLE, work, ignore=shutil.ignore_patterns(
        'build', 'evolve', '.gopyt-state', '.gopyt-transaction.lock'))
    return str(work)


class AuthExample(unittest.TestCase):
    def setUp(self) -> None:
        self.root = stage()

    def tearDown(self) -> None:
        shutil.rmtree(Path(self.root).parent, ignore_errors=True)

    def test_check_is_clean_with_the_committed_lock(self) -> None:
        code, out = run_cli(self.root, "check")
        self.assertEqual(code, 0, out)

    def test_tests_pass(self) -> None:
        code, out = run_cli(self.root, "test")
        self.assertEqual(code, 0, out)

    def test_burst_sets_cusum_alarm(self) -> None:
        code, out = run_cli(self.root, "run", "auth.burst")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "true\n")

    def test_observe_buffers_stay_bounded(self) -> None:
        prog, art, fn_ids = build(self.root)
        vm = make_vm(self.root, prog, art, fn_ids)
        cells = vm.observe.memory_cells()
        self.assertTrue(vm.call(fn_ids["auth.burst"], []))
        self.assertEqual(vm.observe.memory_cells(), cells)
        self.assertLessEqual(len(vm.observe.reservoir.items), vm.observe.reservoir.k)
        self.assertGreater(vm.observe.fail, 0)
        # A second burst adds events but not memory (P0: O(1) in stream length).
        events = vm.observe.events
        vm.call(fn_ids["auth.burst"], [])
        self.assertGreater(vm.observe.events, events)
        self.assertEqual(vm.observe.memory_cells(), cells)

    def test_no_app_ffi_and_no_egress(self) -> None:
        from gopyt import ops
        from gopyt.gobyte import decode

        self.assertEqual(run_cli(self.root, "check")[0], 0)
        art = decode(Path(self.root, "build/out.gobyte").read_bytes())
        self.assertEqual(art.egress, [])
        for f in art.funcs:
            name = art.const_str(f.name)
            if name.startswith("auth.") or name.startswith("test."):
                self.assertFalse(f.effects & ops.FFI_BIT, name)

    def test_the_committed_sources_are_canonical(self) -> None:
        """`gopyt fmt` must be a no-op on what is committed (E004 otherwise)."""
        self.assert_canonical_sources(EXAMPLE, Path(self.root))

    def assert_canonical_sources(self, original: Path, staged: Path) -> None:
        from gopyt.cli import cmd_fmt

        cmd_fmt(str(staged))
        for tree in ('spec', 'impl', 'test'):
            for src in sorted((original / tree).rglob("*.gopyt")):
                rel = src.relative_to(original)
                self.assertEqual(
                    src.read_bytes(), (staged / rel).read_bytes(), str(rel)
                )

    def test_staging_does_not_copy_a_local_demo_database_or_build_cache(self) -> None:
        """Running the example locally must not contaminate isolated tests."""
        for name in ('build', 'evolve', '.gopyt-state'):
            directory = Path(self.root, name)
            directory.mkdir(exist_ok=True)
            (directory / 'private-data.gopyt').write_text('local demo state')
        Path(self.root, '.gopyt-transaction.lock').touch()
        with patch(__name__ + '.EXAMPLE', Path(self.root)):
            copied = Path(stage())
        try:
            for name in ('build', 'evolve', '.gopyt-state', '.gopyt-transaction.lock'):
                self.assertFalse((copied / name).exists(), name)
            self.assertEqual((copied / 'gopyt.toml').read_bytes(), Path(self.root, 'gopyt.toml').read_bytes())
            self.assertTrue((copied / 'impl/auth.gopyt').is_file())
            self.assert_canonical_sources(Path(self.root), copied)
        finally:
            shutil.rmtree(copied.parent)

    def test_login_denies_then_throttles(self) -> None:
        """Distinct users so the bucket state of one case cannot leak."""
        prog, art, fn_ids = build(self.root)
        vm = make_vm(self.root, prog, art, fn_ids)
        from gopyt.values import Record

        login = fn_ids["auth.login"]
        req_type = next(
            i for i, td in enumerate(art.types) if art.const_str(td.name) == "auth.LoginReq"
        )
        req = Record(req_type, ["dave", "wrong"])
        first = vm.call(login, [req])
        self.assertEqual(vm.type_name(first.type_id), "auth.Denied")
        for _ in range(6):
            last = vm.call(login, [req])
        self.assertEqual(vm.type_name(last.type_id), "core.status.Throttled")


if __name__ == "__main__":
    unittest.main()
