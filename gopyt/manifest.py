"""gopyt.toml / gopyt.lock (docs/lockfile.md).

A deliberately closed TOML subset: exactly the forms the document shows.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass, field

from gopyt.diag import CompileError, Diag
from gopyt.files import regular_file

TOOLCHAIN = "gopyt-0.1.000"
SOURCE_DIRS = ("spec", "impl", "test")


@dataclass
class Manifest:
    name: str
    version: str
    deps: dict[str, str] = field(default_factory=dict)  # key -> path as written
    root: str = ""


def _e046(root: str) -> CompileError:
    return CompileError(Diag(46, "gopyt.toml", 1, 0))


def _is_snake(s: str) -> bool:
    return 2 <= len(s) <= 64 and re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", s) is not None


def _is_version(s: str) -> bool:
    parts = s.split(".")
    if len(parts) != 3:
        return False
    for p in parts:
        if not p.isascii() or not p.isdigit() or len(p) > 10 or (len(p) > 1 and p[0] == "0"):
            return False
        if int(p) > 2147483647:
            return False
    return True


def _dep_path_ok(p: str) -> bool:
    if p.startswith("/") or any(c in p for c in ('\\', ':', '"', '\0', '\n', '\r', '\t')):
        return False
    segs = p.split("/")
    if segs[0] == "..":
        return len(segs) == 2 and _is_snake(segs[1])
    return all(s and s != "." and s != ".." for s in segs)


def parse_manifest(root: str) -> Manifest:
    path = os.path.join(root, "gopyt.toml")
    reject_symlinks(path)
    try:
        with regular_file(root, "gopyt.toml") as fh:
            raw = fh.read()
    except OSError:
        raise CompileError(Diag(46, "gopyt.toml", 1, 0))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise CompileError(Diag(3, "gopyt.toml", 1, 0))
    if "\r" in text or "\t" in text or not text.endswith("\n"):
        raise _e046(root)
    lines = text.split("\n")[:-1]
    if len(lines) < 2:
        raise _e046(root)
    name = _kv(lines[0], "name")
    version = _kv(lines[1], "version")
    if not _is_snake(name) or not _is_version(version):
        raise _e046(root)
    deps: dict[str, str] = {}
    rest = lines[2:]
    if rest:
        if rest[0] != "" or len(rest) < 2 or rest[1] != "[deps]":
            raise _e046(root)
        seen: set[str] = set()
        prev = ""
        for ln in rest[2:]:
            key, dep_path = _dep_line(ln)
            if key in seen or not _is_snake(key) or not _dep_path_ok(dep_path):
                raise _e046(root)
            if prev and key.encode("utf-8") <= prev.encode("utf-8"):
                raise _e046(root)
            prev = key
            seen.add(key)
            deps[key] = dep_path
        if not deps:
            raise _e046(root)
    return Manifest(name=name, version=version, deps=deps, root=root)


def _kv(line: str, key: str) -> str:
    prefix = f'{key} = "'
    if not line.startswith(prefix) or not line.endswith('"'):
        raise CompileError(Diag(46, "gopyt.toml", 1, 0))
    value = line[len(prefix) : -1]
    if '"' in value or "\\" in value:
        raise CompileError(Diag(46, "gopyt.toml", 1, 0))
    return value


def _dep_line(line: str) -> tuple[str, str]:
    marker = ' = { path = "'
    if marker not in line or not line.endswith('" }'):
        raise CompileError(Diag(46, "gopyt.toml", 1, 0))
    key, rest = line.split(marker, 1)
    return key, rest[: -len('" }')]


def manifest_text(man: Manifest) -> str:
    out = [f'name = "{man.name}"', f'version = "{man.version}"']
    if man.deps:
        out.append("")
        out.append("[deps]")
        for key in sorted(man.deps, key=lambda k: k.encode("utf-8")):
            out.append(f'{key} = {{ path = "{man.deps[key]}" }}')
    return "\n".join(out) + "\n"


def package_digest(root: str, expected: dict[str, bytes] | None = None) -> str:
    """sha256 over gopyt.toml then every regular file in spec/, impl/, test/."""
    validate_source_tree(root)
    entries: list[tuple[str, str]] = [("gopyt.toml", os.path.join(root, "gopyt.toml"))]
    for folder in SOURCE_DIRS:
        base = os.path.join(root, folder)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, files in os.walk(base):
            dirnames.sort()
            for fn in sorted(files):
                full = os.path.join(dirpath, fn)
                if os.path.islink(full) or not os.path.isfile(full):
                    raise CompileError(Diag(46, folder + "/" + fn, 1, 0))
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                entries.append((rel, full))
    entries.sort(key=lambda e: e[0].encode("utf-8"))
    if expected is not None:
        source_names = {rel for rel, _full in entries if rel.endswith(".gopyt") or rel == "gopyt.toml"}
        if source_names != set(expected):
            raise CompileError(Diag(46, "gopyt.toml", 1))
    pre = hashlib.sha256()
    for rel, full in entries:
        with regular_file(root, rel) as fh:
            data = fh.read()
        if expected is not None and rel in expected and data != expected[rel]:
            raise CompileError(Diag(46, rel, 1))
        pre.update(rel.encode("utf-8") + b"\n")
        pre.update(b"sha256:" + hashlib.sha256(data).hexdigest().encode("ascii") + b"\n")
    return "sha256:" + pre.hexdigest()


def reject_symlinks(path: str) -> None:
    """Inspect lexical components, before any read, walk, or rewrite."""
    full = os.path.abspath(path)
    current = os.path.sep
    for segment in full.split(os.path.sep)[1:]:
        current = os.path.join(current, segment)
        if os.path.islink(current):
            raise CompileError(Diag(46, os.path.basename(path), 1, 0))


def validate_source_tree(root: str) -> None:
    reject_symlinks(os.path.join(root, "gopyt.toml"))
    for folder in SOURCE_DIRS:
        base = os.path.join(root, folder)
        if not os.path.lexists(base):
            continue
        reject_symlinks(base)
        if not os.path.isdir(base):
            raise CompileError(Diag(46, folder, 1, 0))
        for dirpath, dirnames, files in os.walk(base):
            for name in sorted(dirnames + files):
                if "\n" in name or "\r" in name:
                    # Paths are line-delimited in the frozen digest preimage.
                    # A newline could otherwise impersonate another entry.
                    raise CompileError(Diag(46, folder, 1, 0))
                full = os.path.join(dirpath, name)
                mode = os.lstat(full).st_mode
                if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                    raise CompileError(Diag(46, os.path.relpath(full, root), 1, 0))


def lock_text(packages: list[tuple[str, str, str, str]]) -> str:
    """packages: (name, version, path, digest); root first, then deps by name."""
    out = [f'toolchain = "{TOOLCHAIN}"']
    for name, version, path, digest in packages:
        out.append("")
        out.append("[[pkg]]")
        out.append(f'name = "{name}"')
        out.append(f'version = "{version}"')
        out.append(f'path = "{path}"')
        out.append(f'digest = "{digest}"')
    return "\n".join(out) + "\n"
