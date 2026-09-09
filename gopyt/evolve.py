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
import socket
import secrets
import sys
from contextlib import contextmanager
import time
from dataclasses import dataclass, fields, is_dataclass

from gopyt.check import check_package, load_package
from gopyt.diag import CompileError
from gopyt.manifest import lock_text, package_digest, reject_symlinks
from gopyt.files import atomic_write, segments, regular_file, parent_directory
from gopyt.transaction import guard, guarded, commit

SKIP_DIRS = ("build", "evolve", ".git")


@dataclass
class Outcome:
    kind: str  # NoChange | Applied | EvolveError
    digest: str = ""
    message: str = ""
    candidate_digest: str = ""



MAX_OUTCOME_BYTES = 16_384
_OUTCOME_FIELDS = ('kind', 'digest', 'message', 'candidate_digest')


class _WaveTimeout(Exception):
    pass


class _WaveBudget:
    def __init__(self, context, timeout_ms):
        self.context = context
        self.deadline_ns = None if timeout_ms is None else time.monotonic_ns() + timeout_ms * 1_000_000
        if context is not None and context.deadline_ns is not None:
            self.deadline_ns = (context.deadline_ns if self.deadline_ns is None else
                                min(self.deadline_ns, context.deadline_ns))

    def check_cancelled(self):
        if self.context is not None:
            self.context.check_cancelled()
        if self.deadline_ns is not None and time.monotonic_ns() >= self.deadline_ns:
            raise _WaveTimeout()

    def remaining(self):
        self.check_cancelled()
        if self.deadline_ns is None:
            return .05
        left = self.deadline_ns - time.monotonic_ns()
        if left <= 0:
            self.check_cancelled()
            raise _WaveTimeout()
        return min(.05, left / 1_000_000_000)


def _outcome_record(record):
    if (not isinstance(record, dict) or set(record) != set(_OUTCOME_FIELDS)
            or any(type(record[name]) is not str for name in _OUTCOME_FIELDS)
            or record['kind'] not in ('Prepared', 'NoChange', 'EvolveError')):
        raise ValueError('invalid preparation outcome')
    return Outcome(**record)


class _OutcomeWriter:
    """Spawn transfers only the socket; results use a bounded JSON frame."""
    def __init__(self, sock):
        self.sock = sock

    def send(self, outcome):
        record = {name: getattr(outcome, name) for name in _OUTCOME_FIELDS}
        _outcome_record(record)
        payload = json.dumps(record, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(payload) > MAX_OUTCOME_BYTES:
            raise ValueError('preparation outcome too large')
        self.sock.sendall(len(payload).to_bytes(4, 'big') + payload)

    def close(self):
        self.sock.close()


def _receive_outcome(sock, budget):
    def receive(size):
        while True:
            sock.settimeout(budget.remaining())
            try:
                data = sock.recv(size)
                budget.check_cancelled()
                return data
            except TimeoutError:
                budget.check_cancelled()

    def exact(size):
        data = bytearray()
        while len(data) < size:
            chunk = receive(size - len(data))
            if not chunk:
                raise EOFError('incomplete preparation outcome')
            data.extend(chunk)
        return bytes(data)

    def unique(pairs):
        record = {}
        for key, value in pairs:
            if key in record:
                raise ValueError('duplicate outcome field')
            record[key] = value
        return record

    size = int.from_bytes(exact(4), 'big')
    if not 0 < size <= MAX_OUTCOME_BYTES:
        raise OSError('preparation outcome size')
    payload = exact(size)
    if receive(1):
        raise OSError('trailing preparation outcome')
    try:
        return _outcome_record(json.loads(payload.decode('utf-8'), object_pairs_hook=unique))
    except (ValueError, TypeError, RecursionError) as exc:
        raise OSError('preparation outcome format') from exc


@contextmanager
def _proposal_workspace(root):
    """Own only this proposal's staging tree, never another wave's cache."""
    with parent_directory(root, 'evolve/.workspace', create=True) as (parent, _):
        name = '.work-' + secrets.token_hex(16)
        os.mkdir(name, 0o700, dir_fd=parent)
        identity = os.stat(name, dir_fd=parent, follow_symlinks=False)
        try:
            yield os.path.join(root, 'evolve', name)
        finally:
            primary = sys.exc_info()[1]
            try:
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                    raise OSError('proposal workspace changed')
                shutil.rmtree(name, dir_fd=parent)
            except OSError as error:
                if primary is None:
                    raise
                primary.add_note('proposal cleanup failed: ' + type(error).__name__)


def _prepare_child(root, max_candidates, module, traces, budget, workspace=None):
    import multiprocessing
    budget.check_cancelled()
    reader, sender = socket.socketpair()
    process = None
    received = False
    try:
        writer = _OutcomeWriter(sender)
        process = multiprocessing.get_context('spawn').Process(
            target=_prepare_worker, args=(writer, root, max_candidates, module, traces, workspace))
        budget.check_cancelled()
        process.start()
        writer.close()
        budget.check_cancelled()
        plan = _receive_outcome(reader, budget)
        received = True
        return plan
    finally:
        sender.close()
        reader.close()
        if process is not None:
            try:
                if process.pid is not None:
                    if received:
                        process.join(.05)
                    if process.is_alive():
                        try:
                            process.terminate()
                        except ProcessLookupError:
                            pass
                        process.join(.25)
                        if process.is_alive():
                            try:
                                process.kill()
                            except ProcessLookupError:
                                pass
                    process.join()
            finally:
                process.close()


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
def _prepare(root: str, max_candidates: int, module: str | None = None, traces=None,
             workspace: str | None = None) -> Outcome:
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
    wave = os.path.join(workspace or os.path.join(root, "evolve"), digest.replace("sha256:", ""))
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


def _prepare_worker(connection, root, max_candidates, module, traces, workspace=None):
    try:
        connection.send(_prepare(root, max_candidates, module, traces, workspace))
    except Exception as exc:
        connection.send(Outcome("EvolveError", message=type(exc).__name__))
    finally:
        connection.close()


def _apply(root, plan, *, context=None):
    with guard(root, context=context):
        return _apply_locked(root, plan, context)


def _apply_locked(root, plan, context):
    def check_context():
        if context is not None:
            context.check_cancelled()

    check_context()
    chosen = plan.message
    if not plan.candidate_digest or package_digest(chosen) != plan.candidate_digest:
        return Outcome("EvolveError", message="candidate changed")
    check_context()
    base_egress, base_ffi = _survey(root)
    check_context()
    new_egress, new_ffi = _survey(chosen)
    if new_egress != base_egress or new_ffi - base_ffi:
        return Outcome("EvolveError", message="candidate authority changed")
    if load_package(root).packages[1:] != load_package(chosen).packages[1:]:
        return Outcome("EvolveError", message="dependencies changed")
    if package_digest(root) != plan.digest:
        return Outcome("EvolveError", message="source changed")
    check_context()
    changes = []
    for folder in ("spec", "impl", "test"):
        source = os.path.join(chosen, folder)
        for dirpath, _dirs, names in os.walk(source):
            for name in sorted(names):
                check_context()
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
    check_context()
    try:
        # Once journal publication begins, finish commit/recovery cleanup.
        commit(root, changes, writer=atomic_write)
    except OSError:
        return Outcome("EvolveError", message="apply failed")
    result = Outcome("Applied", digest=package_digest(root))
    check_context()
    return result


def propose(root: str, alarm: bool, max_candidates: int,
            timeout_ms: int | None = None, module: str | None = None, traces=None,
            context=None) -> Outcome:
    """Bound preparation and apply admission; finish admitted journal cleanup."""
    if not alarm:
        return Outcome("NoChange")
    if max_candidates < 1:
        return Outcome("EvolveError", message="max")
    budget = None
    try:
        if timeout_ms is not None or context is not None:
            budget = _WaveBudget(context, timeout_ms)
            budget.check_cancelled()
        with _proposal_workspace(root) as workspace:
            if budget is None:
                plan = _prepare(root, max_candidates, module, traces, workspace)
            else:
                budget.check_cancelled()
                plan = _prepare_child(root, max_candidates, module, traces, budget, workspace)
                budget.check_cancelled()
            result = _apply(root, plan, context=budget) if plan.kind == "Prepared" else plan
        if budget is not None:
            budget.check_cancelled()
        return result
    except _WaveTimeout:
        return Outcome("EvolveError", message="timeout")
    except (CompileError, OSError, EOFError) as exc:
        return Outcome("EvolveError", message=type(exc).__name__)
