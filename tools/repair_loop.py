#!/usr/bin/env python3
"""P2 without a chat agent (docs/validation.md).

Runs `gopyt check`, applies the diagnostic's own repair bytes, and repeats. It
may only apply `GOPYT_E*` repairs, so it cannot invent a dialect: when a
diagnostic carries no repair bytes the loop stops and reports the code.

    python3 tools/repair_loop.py <package-dir>

Exit 0 means the package now checks clean.
"""

from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gopyt.cli import cmd_fmt, main  # noqa: E402

MAX_CYCLES = 20


@dataclass
class Diagnostic:
    code: str
    file: str | None
    repair: str


def parse(text: str) -> Diagnostic | None:
    if not text.startswith("GOPYT_E"):
        return None
    header, _, body = text.partition("\n\n")
    lines = header.split("\n")
    code = lines[0].split(" ", 1)[0]
    file = None
    nbytes = 0
    for line in lines[1:]:
        if line.startswith("file: "):
            file = line[len("file: ") :]
        elif line.startswith("repair-bytes: "):
            nbytes = int(line.split()[1])
    return Diagnostic(code, file, body if nbytes else "")


def check(root: str) -> tuple[int, str]:
    cwd = os.getcwd()
    buf = io.StringIO()
    os.chdir(root)
    try:
        with redirect_stdout(buf):
            status = main(["check"])
    finally:
        os.chdir(cwd)
    return status, buf.getvalue()


def apply(root: str, diag: Diagnostic) -> str:
    """Apply one repair. Returns a note, or "" when the loop must stop."""
    if not diag.repair:
        return ""
    text = diag.repair
    if text.startswith("toolchain = "):
        Path(root, "gopyt.lock").write_text(text, encoding="utf-8")
        return "wrote gopyt.lock"
    if diag.file is None:
        return ""
    target = Path(root, *diag.file.split("/"))
    if text.startswith("module "):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return f"wrote {diag.file}"
    if text.startswith("use ") and text.endswith("\n") and text.count("\n") == 1:
        lines = target.read_text(encoding="utf-8").split("\n")
        insert = 1
        for i, line in enumerate(lines):
            if line.startswith("use "):
                insert = i + 1
            elif line.startswith("module "):
                insert = max(insert, i + 1)
        keep = text.rstrip("\n")
        replaced = False
        prefix = keep.split("{", 1)[0]
        for i, line in enumerate(lines):
            if line.startswith(prefix) and line.startswith("use "):
                lines[i] = keep  # the repair widens an existing allowlist
                replaced = True
                break
        if not replaced:
            lines.insert(insert, keep)
        target.write_text("\n".join(lines), encoding="utf-8")
        return f"added {keep!r} to {diag.file}"
    return ""


def run(root: str, cycles: int = MAX_CYCLES) -> tuple[int, list[str]]:
    log: list[str] = []
    for _ in range(cycles):
        try:
            cmd_fmt(root)
        except Exception:
            pass  # a file too broken to parse is reported by check below
        status, out = check(root)
        if status == 0:
            log.append("check clean")
            return 0, log
        diag = parse(out)
        if diag is None:
            log.append("no diagnostic")
            return 1, log
        note = apply(root, diag)
        if not note:
            log.append(f"{diag.code}: no applicable repair")
            return 1, log
        log.append(f"{diag.code}: {note}")
    log.append("cycle limit reached")
    return 1, log


def main_cli(argv: list[str]) -> int:
    if len(argv) != 1:
        sys.stderr.write("usage: repair_loop.py <package-dir>\n")
        return 2
    status, log = run(argv[0])
    for line in log:
        print(line)
    return status


if __name__ == "__main__":
    raise SystemExit(main_cli(sys.argv[1:]))
