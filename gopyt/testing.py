"""Test helpers: build a package on disk the way docs/conformance.md describes."""

from __future__ import annotations

import io
import os
from contextlib import redirect_stdout
from pathlib import Path

from gopyt.check import Program, check_package, load_package
from gopyt.cli import cmd_fmt, main
from gopyt.diag import CompileError
from gopyt.manifest import lock_text


def write_pkg(
    root: str,
    files: dict[str, str],
    name: str = "test_package",
    version: str = "0.1.0",
    lock: bool = True,
    fmt: bool = False,
) -> None:
    Path(root, "gopyt.toml").write_text(f'name = "{name}"\nversion = "{version}"\n', encoding="utf-8")
    for rel, src in files.items():
        path = Path(root, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(src, encoding="utf-8")
    if fmt:
        cmd_fmt(root)
    if lock:
        write_lock(root)


def write_lock(root: str) -> None:
    pkg = load_package(root)
    Path(root, "gopyt.lock").write_text(lock_text(pkg.packages), encoding="utf-8")


def check(root: str) -> Program:
    return check_package(load_package(root))


def diag_code(root: str) -> int:
    """The one code `gopyt check` reports, or 0 when the package is clean."""
    try:
        check(root)
    except CompileError as e:
        return e.diag.code
    return 0


def diag(root: str) -> CompileError | None:
    try:
        check(root)
    except CompileError as e:
        return e
    return None


def run_cli(root: str, *argv: str, allow_internal: bool = False) -> tuple[int, str]:
    cwd = os.getcwd()
    buf = io.StringIO()
    os.chdir(root)
    try:
        with redirect_stdout(buf):
            code = main(list(argv))
    finally:
        os.chdir(cwd)
    out = buf.getvalue()
    if not allow_internal and "GOPYT_E001" in out:
        # The CLI turns an unexpected exception into E001 (S27). In tests that
        # is a compiler bug hiding behind a diagnostic, so make it loud.
        raise AssertionError(f"gopyt {' '.join(argv)} reported an internal error:\n{out}")
    return code, out
