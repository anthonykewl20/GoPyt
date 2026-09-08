#!/usr/bin/env python3
"""Fail if gopyt/stdlib_src.py drifts from docs/stdlib.md module fences."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gopyt.stdlib_src import STDLIB_SOURCE  # noqa: E402


def modules_from_doc(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for block in re.findall(r"```\n(module .+?)\n```", text, flags=re.S):
        first = block.split("\n", 1)[0]
        if not first.startswith("module "):
            continue
        name = first[len("module ") :].strip()
        src = block.strip() + "\n"
        out[name] = src
    return out


def main() -> int:
    doc = (ROOT / "docs" / "stdlib.md").read_text(encoding="utf-8")
    expected = modules_from_doc(doc)
    missing = sorted(set(expected) - set(STDLIB_SOURCE))
    extra = sorted(set(STDLIB_SOURCE) - set(expected))
    diffs = []
    for name in sorted(set(expected) & set(STDLIB_SOURCE)):
        if expected[name] != STDLIB_SOURCE[name]:
            diffs.append(name)
    if missing or extra or diffs:
        print("stdlib_src.py out of sync with docs/stdlib.md")
        if missing:
            print("missing:", ", ".join(missing))
        if extra:
            print("extra:", ", ".join(extra))
        if diffs:
            print("text differs:", ", ".join(diffs))
        return 1
    print(f"ok {len(expected)} modules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
