"""Path dependencies and the lock graph (docs/lockfile.md).

Fixtures live in gopyt/testdata/deps/. Each case is copied to a temporary tree
so a written lock never lands in the repository.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from gopyt.check import load_package
from gopyt.diag import CompileError
from gopyt.manifest import lock_text
from gopyt.testing import diag, run_cli

DATA = Path(__file__).resolve().parent / "testdata" / "deps"


def stage(*names: str) -> str:
    """Copy the named packages side by side; return the first one's path."""
    work = Path(tempfile.mkdtemp())
    for name in names:
        shutil.copytree(DATA / name, work / name)
    return str(work / names[0])


class Graph(unittest.TestCase):
    def code(self, *names: str) -> int:
        try:
            load_package(stage(*names))
        except CompileError as e:
            return e.diag.code
        return 0

    def test_e042_cycle(self) -> None:
        self.assertEqual(self.code("cycle_a", "cycle_b"), 42)

    def test_e043_missing_path(self) -> None:
        self.assertEqual(self.code("app_missing", "auth"), 43)

    def test_e044_name_mismatch(self) -> None:
        self.assertEqual(self.code("app_wrong_name", "auth"), 44)

    def test_e045_same_name_two_paths(self) -> None:
        self.assertEqual(self.code("app_dup", "auth", "auth_alt", "mid"), 45)


class Resolved(unittest.TestCase):
    def setUp(self) -> None:
        self.root = stage("app_ok", "auth")

    def test_lock_lists_root_first_then_deps_by_name(self) -> None:
        pkg = load_package(self.root)
        self.assertEqual([p[0] for p in pkg.packages], ["app_ok", "auth"])
        self.assertEqual(pkg.packages[0][2], ".")
        self.assertEqual(pkg.packages[1][2], "../auth")
        for _n, _v, _p, digest in pkg.packages:
            self.assertTrue(digest.startswith("sha256:"))

    def test_missing_lock_is_e041_then_check_passes(self) -> None:
        err = diag(self.root)
        self.assertEqual(err.diag.code, 41)
        self.assertEqual(err.diag.repair.count("[[pkg]]"), 2)
        Path(self.root, "gopyt.lock").write_text(err.diag.repair, encoding="utf-8")
        self.assertEqual(run_cli(self.root, "check")[0], 0)

    def test_dep_module_is_callable_and_reaches_bytecode(self) -> None:
        pkg = load_package(self.root)
        Path(self.root, "gopyt.lock").write_text(lock_text(pkg.packages), encoding="utf-8")
        self.assertEqual(run_cli(self.root, "check")[0], 0)
        from gopyt.gobyte import decode

        art = decode(Path(self.root, "build/out.gobyte").read_bytes())
        names = {art.const_str(f.name) for f in art.funcs}
        self.assertIn("auth.tag", names)
        self.assertIn("app.label", names)

    def test_dep_digest_tracks_dep_sources(self) -> None:
        before = load_package(self.root).packages[1][3]
        impl = Path(self.root).parent / "auth" / "impl" / "auth.gopyt"
        impl.write_text(impl.read_text(encoding="utf-8") + "\n// touched\n", encoding="utf-8")
        after = load_package(self.root).packages[1][3]
        self.assertNotEqual(before, after)


class DirectImports(unittest.TestCase):
    """lockfile.md: a transitive package must also be a direct dependency."""

    def test_reaching_past_a_dependency_is_e043(self) -> None:
        root = stage("app_transitive", "mid", "auth")
        from gopyt.testing import diag_code

        self.assertEqual(diag_code(root), 43)

    def test_declaring_the_dependency_makes_the_import_legal(self) -> None:
        root = stage("app_transitive", "mid", "auth")
        toml = Path(root, "gopyt.toml")
        toml.write_text(
            'name = "app_transitive"\nversion = "0.1.0"\n\n[deps]\n'
            'auth = { path = "../auth" }\nmid = { path = "../mid" }\n',
            encoding="utf-8",
        )
        from gopyt.testing import diag

        err = diag(root)
        self.assertEqual(err.diag.code, 41)  # only the lock is left
        Path(root, "gopyt.lock").write_text(err.diag.repair, encoding="utf-8")
        self.assertEqual(run_cli(root, "check")[0], 0)


class Names(unittest.TestCase):
    """A package may not declare a stdlib prefix or a dependency's name."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def test_a_stdlib_prefix_may_not_be_an_app_module(self) -> None:
        from gopyt.testing import diag_code, write_pkg

        write_pkg(
            self.td,
            {"spec/core/list.gopyt": "module core.list\n\nfn nothing() -> unit\n"},
            lock=False,
        )
        self.assertEqual(diag_code(self.td), 12)

    def test_a_root_module_may_not_shadow_a_dependency(self) -> None:
        root = stage("app_ok", "auth")
        Path(root, "spec/auth.gopyt").write_text(
            "module auth\n\nfn shadow() -> unit\n", encoding="utf-8"
        )
        from gopyt.testing import diag_code

        self.assertEqual(diag_code(root), 44)


if __name__ == "__main__":
    unittest.main()
