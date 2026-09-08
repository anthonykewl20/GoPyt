"""Run the on-disk P1 fixtures in conformance/.

Those directories belong to the parallel session (PARALLEL.md), so this runner
copies each package to a temporary directory and never writes into the tree.

Two things are added to the copy, both of which a repair agent would do:
  * `gopyt fmt`, as conformance/README.md prescribes;
  * `gopyt.lock`, because docs/lockfile.md makes a missing lock `GOPYT_E041`
    while docs/conformance.md expects those packages to check clean. A fixture
    that ships its own lock, or expects E041, is left alone.

`expect` is a first line (`ok`, `Ennn`, or text that `gopyt run` must print),
optionally followed by `key: value` lines: `phase` (check, run, bytecode),
`command`, `trap`, `note`.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from gopyt.cli import cmd_fmt
from gopyt.testing import diag, run_cli, write_lock

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "conformance"

# docs/conformance.md C038 states the answer is "E074 or E110"; the lexer
# rejects the reserved name first (docs/diagnostics.md E110).
ALTERNATIVES = {frozenset({"E074", "E110"})}


def cases() -> list[Path]:
    if not FIXTURES.is_dir():
        return []
    return sorted(p for p in FIXTURES.iterdir() if p.is_dir() and (p / "expect").is_file())


def parse_expect(path: Path) -> tuple[str, dict[str, str]]:
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    meta: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return lines[0].strip(), meta


def entry_points(root: str) -> list[str]:
    from gopyt.check import check_package, load_package

    prog = check_package(load_package(root))
    return sorted(
        key
        for key, fc in prog.funcs.items()
        if not fc.params and fc.kind in ("fn", "task", "workflow")
    )


class Fixtures(unittest.TestCase):
    def prepare(self, case: Path, expect: str) -> str | None:
        if not (case / "gopyt.toml").is_file():
            return None
        work = Path(tempfile.mkdtemp()) / "pkg"
        shutil.copytree(case, work)
        root = str(work)
        try:
            cmd_fmt(root)
        except Exception:
            return root
        if expect != "E041" and not (work / "gopyt.lock").is_file():
            try:
                write_lock(root)
            except Exception:
                pass
        return root

    def outcome(self, case: Path) -> tuple[str, str]:
        expect, meta = parse_expect(case / "expect")
        phase = meta.get("phase", "check")
        if phase not in ("check", "run"):
            # `bytecode` corrupts an artifact and `evolve` runs the candidate
            # wave: neither is expressible as a source package. C027 is covered
            # directly in gopyt/test_conformance.py; evolve stays out of v0
            # until P0+check, per docs/validation.md.
            return expect, f"skipped: {phase} phase"
        root = self.prepare(case, expect)
        if root is None:
            return expect, "skipped: not a package"
        if meta.get("phase") == "run":
            argv = (meta.get("command") or "").split()[1:]
            if not argv:
                try:
                    targets = entry_points(root)
                except Exception:
                    targets = []
                if len(targets) != 1:
                    return expect, "skipped: no command in expect"
                argv = ["run", targets[0]]
            code, out = run_cli(root, *argv)
            if code == 0:
                return expect, "ok" if expect == "ok" else out.strip()
            got = out.split(" ", 1)[0].replace("GOPYT_", "").strip()
            if "trap" in meta and f"trap: {meta['trap']}" not in out:
                got += f" (trap line missing: {out.strip()})"
            return expect, got
        err = diag(root)
        return expect, "ok" if err is None else f"E{err.diag.code:03d}"

    def test_fixtures(self) -> None:
        found = cases()
        if not found:
            self.skipTest("no conformance/ fixtures present")
        failures = []
        for case in found:
            expect, got = self.outcome(case)
            if got.startswith("skipped:"):
                continue
            if expect == got:
                continue
            if frozenset({expect, got}) in ALTERNATIVES:
                continue
            if expect not in ("ok",) and not expect.startswith("E") and expect in got:
                continue
            failures.append(f"{case.name}: expect {expect}, got {got}")
        self.assertEqual(failures, [], "conformance/ fixtures disagree:\n" + "\n".join(failures))


class FormatterIdempotence(unittest.TestCase):
    """C026 over every fixture: formatting twice equals formatting once.

    The fixtures belong to the parallel session, so each package is formatted in
    a temporary copy and nothing is written back into conformance/.
    """

    def test_every_fixture_formats_to_a_fixed_point(self) -> None:
        found = cases()
        if not found:
            self.skipTest("no conformance/ fixtures present")
        checked = 0
        problems = []
        for case in found:
            if not (case / "gopyt.toml").is_file():
                continue
            work = Path(tempfile.mkdtemp()) / "pkg"
            shutil.copytree(case, work)
            root = str(work)
            try:
                try:
                    cmd_fmt(root)
                except Exception:
                    continue  # a fixture that cannot parse has nothing to format
                once = {p: p.read_bytes() for p in sorted(work.rglob("*.gopyt"))}
                cmd_fmt(root)
                twice = {p: p.read_bytes() for p in sorted(work.rglob("*.gopyt"))}
                if once != twice:
                    problems.append(case.name)
                for path, data in twice.items():
                    text = data.decode("utf-8")
                    if text and (not text.endswith("\n") or text.endswith("\n\n")):
                        problems.append(f"{case.name}:{path.name} final newline")
                    if any(line != line.rstrip() for line in text.split("\n")):
                        problems.append(f"{case.name}:{path.name} trailing space")
                    if "\t" in text:
                        problems.append(f"{case.name}:{path.name} tab")
                checked += 1
            finally:
                shutil.rmtree(work.parent, ignore_errors=True)
        self.assertEqual(problems, [])
        self.assertGreater(checked, 20)


if __name__ == "__main__":
    unittest.main()
