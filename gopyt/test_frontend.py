from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from gopyt.check import check_package, load_package
from gopyt.diag import CompileError
from gopyt.fmt import fmt_module


def write_pkg(root: str, files: dict[str, str]) -> None:
    """Frontend cases only: no lock, so callers pass check_lockfile=False."""
    Path(root, "gopyt.toml").write_text(
        'name = "test_package"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    for rel, src in files.items():
        p = Path(root, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")


def roundtrip_fmt(root: str) -> None:
    pkg = load_package(root)
    for group in (pkg.spec, pkg.impl, pkg.tests):
        for m in group.values():
            Path(root, m.file).write_text(fmt_module(m), encoding="utf-8")


class Frontend(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td)

    def test_c001(self) -> None:
        spec = """module math

fn add(left: i64, right: i64) -> i64
    requires true
    ensures result == left + right
"""
        impl = """module math

fn add(left: i64, right: i64) -> i64
    requires true
    ensures result == left + right
{
    return left + right
}
"""
        write_pkg(self.td, {"spec/math.gopyt": spec, "impl/math.gopyt": impl})
        roundtrip_fmt(self.td)
        pkg = load_package(self.td)
        check_package(pkg, check_fmt=True, check_lockfile=False)

    def test_c002_missing_use(self) -> None:
        spec = """module orders

task ping() -> unit
    effects { log }
"""
        impl = """module orders

task ping() -> unit
    effects { log }
{
    return core.log.write("x")
}
"""
        write_pkg(self.td, {"spec/orders.gopyt": spec, "impl/orders.gopyt": impl})
        roundtrip_fmt(self.td)
        pkg = load_package(self.td)
        with self.assertRaises(CompileError) as cm:
            check_package(pkg, check_fmt=False, check_lockfile=False)
        self.assertEqual(cm.exception.diag.code, 13)

    def test_c004_fn_task(self) -> None:
        spec = """module orders

fn ping() -> unit
"""
        impl = """module orders

use core.log { write }

fn ping() -> unit
{
    return core.log.write("x")
}
"""
        write_pkg(self.td, {"spec/orders.gopyt": spec, "impl/orders.gopyt": impl})
        pkg = load_package(self.td)
        with self.assertRaises(CompileError) as cm:
            check_package(pkg, check_fmt=False, check_lockfile=False)
        self.assertEqual(cm.exception.diag.code, 53)

    def test_c005_int(self) -> None:
        src = """module math

fn add(left: int, right: i64) -> i64
"""
        Path(self.td, "spec").mkdir()
        Path(self.td, "spec/math.gopyt").write_text(src, encoding="utf-8")
        Path(self.td, "gopyt.toml").write_text(
            'name = "test_package"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        with self.assertRaises(CompileError) as cm:
            load_package(self.td)
        self.assertEqual(cm.exception.diag.code, 20)

    def test_c006_userid(self) -> None:
        src = """module math

type UserID {
}
"""
        Path(self.td, "spec").mkdir()
        Path(self.td, "spec/math.gopyt").write_text(src, encoding="utf-8")
        Path(self.td, "gopyt.toml").write_text(
            'name = "test_package"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        with self.assertRaises(CompileError) as cm:
            load_package(self.td)
        self.assertEqual(cm.exception.diag.code, 16)

    def test_c007_leading_zero(self) -> None:
        src = """module math

fn add() -> i64
"""
        impl = """module math

fn add() -> i64
{
    return 01
}
"""
        write_pkg(self.td, {"spec/math.gopyt": src, "impl/math.gopyt": impl})
        with self.assertRaises(CompileError) as cm:
            load_package(self.td)
        self.assertEqual(cm.exception.diag.code, 10)

    def test_c008_if_expr(self) -> None:
        spec = """module math

fn add() -> i64
"""
        impl = """module math

fn add() -> i64
{
    number = if true {
        return 1
    } else {
        return 0
    }
    return number
}
"""
        write_pkg(self.td, {"spec/math.gopyt": spec, "impl/math.gopyt": impl})
        with self.assertRaises(CompileError) as cm:
            load_package(self.td)
        self.assertIn(cm.exception.diag.code, (11, 71))

    def test_c009_open(self) -> None:
        spec = """module math

fn add(left: i64, right: i64) -> i64
    requires open needs_gateway
"""
        impl = """module math

fn add(left: i64, right: i64) -> i64
    requires open needs_gateway
{
    return left + right
}
"""
        write_pkg(self.td, {"spec/math.gopyt": spec, "impl/math.gopyt": impl})
        pkg = load_package(self.td)
        with self.assertRaises(CompileError) as cm:
            check_package(pkg, check_fmt=False, check_lockfile=False)
        self.assertEqual(cm.exception.diag.code, 65)

    def test_fmt_idempotent(self) -> None:
        spec = """module math

fn add(left: i64, right: i64) -> i64
    requires true
    ensures result == left + right
"""
        impl = """module math

fn add(left: i64, right: i64) -> i64
    requires true
    ensures result == left + right
{
    return left + right
}
"""
        write_pkg(self.td, {"spec/math.gopyt": spec, "impl/math.gopyt": impl})
        roundtrip_fmt(self.td)
        a = Path(self.td, "spec/math.gopyt").read_text(encoding="utf-8")
        roundtrip_fmt(self.td)
        b = Path(self.td, "spec/math.gopyt").read_text(encoding="utf-8")
        self.assertEqual(a, b)
        pkg = load_package(self.td)
        check_package(pkg, check_fmt=True, check_lockfile=False)

    def test_c013_comma(self) -> None:
        spec = """module math

fn pair(left: i64 right: i64) -> i64
"""
        Path(self.td, "spec").mkdir()
        Path(self.td, "spec/math.gopyt").write_text(spec, encoding="utf-8")
        Path(self.td, "gopyt.toml").write_text(
            'name = "test_package"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        with self.assertRaises(CompileError) as cm:
            load_package(self.td)
        self.assertEqual(cm.exception.diag.code, 11)

    def test_c034_ffi(self) -> None:
        spec = """module math

task ping() -> unit
    effects { ffi }
"""
        impl = """module math

task ping() -> unit
    effects { ffi }
{
    return unit
}
"""
        write_pkg(self.td, {"spec/math.gopyt": spec, "impl/math.gopyt": impl})
        pkg = load_package(self.td)
        with self.assertRaises(CompileError) as cm:
            check_package(pkg, check_fmt=False, check_lockfile=False)
        self.assertEqual(cm.exception.diag.code, 69)


if __name__ == "__main__":
    unittest.main()
