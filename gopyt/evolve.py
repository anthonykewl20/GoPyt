"""`core.evolve.propose` (docs/evolve.md, docs/hardening.md).

Traces -> candidates -> `gopyt fmt` + `gopyt check` + S30 -> new lock digest for
the **next** process. The running artifact is never patched (E116), no model is
called, and no candidate may add `ffi` or drop an `egress` origin.

The candidate generator is mechanical, not a model: it tightens an existing
`core.limit.allow(key, tokens, refill_ms)` bound, which is hardening.md's
"insert / tighten a limiter" move expressed as a pure source rewrite. When a
module has no limiter to tighten there is nothing this build may safely change,
and `propose` answers `NoChange`.
"""

from __future__ import annotations

import os
import json
import shutil
import time
from dataclasses import dataclass, fields, is_dataclass

from gopyt.check import check_package, load_package
from gopyt.diag import CompileError
from gopyt.manifest import lock_text, package_digest, reject_symlinks
from gopyt.files import atomic_write, segments, regular_file
from gopyt.transaction import guarded, commit

SKIP_DIRS = ("build", "evolve", ".git")


@dataclass
class Outcome:
    kind: str  # NoChange | Applied | EvolveError
    digest: str = ""
    message: str = ""
    candidate_digest: str = ""


def _copy_package(src: str, dst: str) -> None:
    from gopyt.manifest import validate_source_tree
    validate_source_tree(src)
    os.makedirs(dst, exist_ok=True)
    paths = ["gopyt.toml"]
    for folder in ("spec", "impl", "test"):
        for directory, dirs, files in os.walk(os.path.join(src, folder)):
            for name in dirs:
                reject_symlinks(os.path.join(directory, name))
            for name in sorted(files):
                paths.append(os.path.relpath(os.path.join(directory, name), src))
    for path in paths:
        with regular_file(src, path) as stream:
            data = stream.read()
        atomic_write(dst, path, data, create_parents=True)


def _impl_files(root: str) -> list[str]:
    base = os.path.join(root, "impl")
    out: list[str] = []
    for dirpath, dirnames, files in os.walk(base):
        dirnames[:] = [d for d in sorted(dirnames) if d not in SKIP_DIRS]
        for name in sorted(files):
            if name.endswith(".gopyt"):
                out.append(os.path.join(dirpath, name))
    return out


def candidates(root: str, limit: int) -> list[list[tuple[str, str]]]:
    """Up to `limit` candidate file sets, each a list of (path, new text)."""
    from gopyt.ast_nodes import CallExpr, IntLit, NameExpr
    from gopyt.parser import parse_module
    from gopyt.fmt import fmt_module

    def tighten(node, divisor):
        if (isinstance(node, CallExpr) and isinstance(node.callee, NameExpr)
                and node.callee.parts == ["core", "limit", "allow"]
                and len(node.args) == 3 and isinstance(node.args[1], IntLit)
                and int(node.args[1].value) > 1):
            node.args[1].value = str(max(1, int(node.args[1].value) // divisor))
        if is_dataclass(node):
            for field in fields(node):
                tighten(getattr(node, field.name), divisor)
        elif isinstance(node, (list, tuple)):
            for item in node:
                tighten(item, divisor)

    out: list[list[tuple[str, str]]] = []
    for divisor in (2, 4, 8, 16):
        if len(out) >= limit:
            break
        edits: list[tuple[str, str]] = []
        for path in _impl_files(root):
            with regular_file(root, os.path.relpath(path, root)) as fh:
                text = fh.read().decode("utf-8")

            module = parse_module(text, os.path.relpath(path, root), "impl")
            tighten(module, divisor)
            new = fmt_module(module)
            if new != text:
                edits.append((os.path.relpath(path, root).replace(os.sep, "/"), new))
        if edits and edits not in out:
            out.append(edits)
    return out


def _survey(root: str):
    """(egress origins, app functions holding ffi) for the S30 comparison."""
    prog = check_package(load_package(root))
    egress = {(m, o) for m, o in prog.egress}
    ffi = {fc.symbol for fc in prog.funcs.values() if "ffi" in fc.effects and fc.kind != "native"}
    return egress, ffi


@guarded
def _write_lock(root: str) -> str:
    pkg = load_package(root)
    text = lock_text(pkg.packages)
    atomic_write(root, "gopyt.lock", text.encode("utf-8"))
    return pkg.packages[0][3]


@guarded
def _prepare(root: str, max_candidates: int, module: str | None = None, traces=None) -> Outcome:
    try:
        base_egress, base_ffi = _survey(root)
    except CompileError as e:
        return Outcome("EvolveError", message=f"E{e.diag.code:03d}")
    digest = package_digest(root)
    sets = candidates(root, max_candidates)
    if module is not None:
        allowed_file = "impl/" + module.replace(".", "/") + ".gopyt"
        sets = [[(rel, text) for rel, text in edits if rel == allowed_file] for edits in sets]
        sets = [edits for edits in sets if edits]
    if not sets:
        return Outcome("NoChange")
    wave = os.path.join(root, "evolve", digest.replace("sha256:", ""))
    reject_symlinks(wave)
    pkg = load_package(root)
    package_paths = [os.path.normpath(os.path.join(root, entry[2])) for entry in pkg.packages]
    common = os.path.commonpath(package_paths)
    from gopyt.observe import replay

    traces = [] if traces is None else traces
    baseline_fitness = replay(traces)
    weights = [1.0] * 4
    try:
        with regular_file(root, "evolve/weights.json") as stream:
            raw_state = stream.read(4097)
        if len(raw_state) > 4096:
            raise ValueError("weight state size")
        state = json.loads(raw_state)
        if (len(state["weights"]) == 4 and all(type(w) in (int, float) and
                0 < w <= 1 for w in state["weights"])):
            weights = state["weights"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    survivors = []
    for index, edits in enumerate(sets):
        snapshot = os.path.join(wave, f"candidate_{index}")
        reject_symlinks(snapshot)
        if os.path.isdir(snapshot):
            shutil.rmtree(snapshot)
        for source in package_paths:
            _copy_package(source, os.path.join(snapshot, os.path.relpath(source, common)))
        work = os.path.join(snapshot, os.path.relpath(root, common))
        for rel, text in edits:
            if segments(rel)[0] not in ("spec", "impl", "test"):
                return Outcome("EvolveError", message="candidate path")
            atomic_write(work, rel, text.encode("utf-8"))
        try:
            from gopyt.cli import cmd_fmt

            cmd_fmt(work)
            _write_lock(work)
            egress, ffi = _survey(work)
            # The gate includes emission and the validating loader, not just
            # the source checker. The running artifact is never replaced.
            from gopyt.cli import build

            build(work)
        except CompileError:
            weights[index] = max(2**-52, weights[index] * 0.5)
            continue  # candidate failed check: discarded, digest unchanged
        if ffi - base_ffi:
            weights[index] = max(2**-52, weights[index] * 0.5)
            continue  # S30: a candidate may not add ffi
        if base_egress != egress:
            weights[index] = max(2**-52, weights[index] * 0.5)
            continue  # S30/P3: neither drop nor widen outbound authority
        fitness = replay(traces)
        if fitness > baseline_fitness:
            weights[index] = max(2**-52, weights[index] * 0.5)
            continue
        survivors.append((weights[index], -index, work))
    atomic_write(root, "evolve/weights.json",
                 json.dumps({"version": 1, "weights": weights,
                             "replay_rows": len(traces), "baseline_peak": baseline_fitness},
                            separators=(",", ":")).encode(), create_parents=True)
    if not survivors:
        return Outcome("EvolveError", message="no candidate passed check")
    return Outcome("Prepared", digest=digest, message=max(survivors)[2],
                   candidate_digest=package_digest(max(survivors)[2]))


def _prepare_worker(connection, root, max_candidates, module, traces):
    try:
        connection.send(_prepare(root, max_candidates, module, traces))
    except Exception as exc:
        connection.send(Outcome("EvolveError", message=type(exc).__name__))
    finally:
        connection.close()


@guarded
def _apply(root, plan):
    chosen = plan.message
    if not plan.candidate_digest or package_digest(chosen) != plan.candidate_digest:
        return Outcome("EvolveError", message="candidate changed")
    base_egress, base_ffi = _survey(root)
    new_egress, new_ffi = _survey(chosen)
    if new_egress != base_egress or new_ffi - base_ffi:
        return Outcome("EvolveError", message="candidate authority changed")
    if load_package(root).packages[1:] != load_package(chosen).packages[1:]:
        return Outcome("EvolveError", message="dependencies changed")
    if package_digest(root) != plan.digest:
        return Outcome("EvolveError", message="source changed")
    changes = []
    for folder in ("spec", "impl", "test"):
        source = os.path.join(chosen, folder)
        for dirpath, _dirs, names in os.walk(source):
            for name in sorted(names):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, chosen)
                with regular_file(chosen, rel) as stream:
                    data = stream.read()
                with regular_file(root, rel) as stream:
                    before = stream.read()
                if data != before:
                    changes.append((rel, before, data))
    with regular_file(root, "gopyt.lock") as stream:
        old_lock = stream.read()
    with regular_file(chosen, "gopyt.lock") as stream:
        new_lock = stream.read()
    changes.append(("gopyt.lock", old_lock, new_lock))
    if package_digest(chosen) != plan.candidate_digest:
        return Outcome("EvolveError", message="candidate changed")
    try:
        commit(root, changes, writer=atomic_write)
    except OSError:
        return Outcome("EvolveError", message="apply failed")
    return Outcome("Applied", digest=package_digest(root))


def propose(root: str, alarm: bool, max_candidates: int,
            timeout_ms: int | None = None, module: str | None = None, traces=None) -> Outcome:
    """Prepare in an interruptible child when called by the VM; apply last.

    Preparation is bounded; a journal makes interruption during commit
    recoverable before any cooperating reader loads the source tree.
    """
    if not alarm:
        return Outcome("NoChange")
    if max_candidates < 1:
        return Outcome("EvolveError", message="max")
    try:
        if timeout_ms is None:
            plan = _prepare(root, max_candidates, module, traces)
        else:
            import multiprocessing

            if timeout_ms < 1:
                return Outcome("EvolveError", message="timeout")
            deadline = time.monotonic() + timeout_ms / 1000
            context = multiprocessing.get_context("spawn")
            reader, writer = context.Pipe(duplex=False)
            process = context.Process(target=_prepare_worker,
                                      args=(writer, root, max_candidates, module, traces))
            process.start()
            writer.close()
            try:
                if not reader.poll(max(0, deadline - time.monotonic())):
                    return Outcome("EvolveError", message="timeout")
                plan = reader.recv()
            finally:
                reader.close()
                if process.is_alive():
                    process.terminate()
                process.join()
            if time.monotonic() >= deadline:
                return Outcome("EvolveError", message="timeout")
        return _apply(root, plan) if plan.kind == "Prepared" else plan
    except (CompileError, OSError, EOFError) as exc:
        return Outcome("EvolveError", message=type(exc).__name__)
