"""The four commands (docs/spec.md S18, docs/implementer.md 9). No flags."""

from __future__ import annotations

import os
import sys

from gopyt.check import Program, check_package, load_package
from gopyt.diag import CompileError, Diag, format_diag
from gopyt.emit import emit_program
from gopyt.fmt import fmt_module
from gopyt.files import atomic_write
from gopyt.transaction import guarded
from gopyt.gobyte import decode, encode
from gopyt.jsonc import ConvertFail, NotJson
from gopyt.values import Unit

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_TRAP = 2


def find_root(start: str) -> str:
    cur = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(cur, "gopyt.toml")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            raise CompileError(Diag(46, None, None, 0))
        cur = parent


@guarded
def cmd_fmt(root: str) -> int:
    pkg = load_package(root)
    for group in (pkg.spec, pkg.impl, pkg.tests):
        for m in group.values():
            try:
                atomic_write(root, m.file, fmt_module(m).encode("utf-8"))
            except OSError:
                raise CompileError(Diag(46, m.file, 1, 0))
    return EXIT_OK


@guarded
def build(root: str):
    """check, then write build/out.gobyte and load it back the way the VM will."""
    pkg = load_package(root)
    prog = check_package(pkg)
    art, fn_ids = emit_program(prog)
    data = encode(art)
    validated = decode(data)
    try:
        atomic_write(root, "build/out.gobyte", data, create_parents=True)
    except OSError:
        raise CompileError(Diag(46, "build/out.gobyte", 1, 0))
    return prog, validated, fn_ids


def make_vm(root: str, prog: Program, art, fn_ids):
    from gopyt.vm import VM

    vm = VM(art, root=root)
    return vm


def cmd_check(root: str) -> int:
    build(root)
    return EXIT_OK


def cmd_run(root: str, target: str) -> int:
    from gopyt import jsonc
    from gopyt.vm import Trap

    prog, art, fn_ids = build(root)
    key = target
    if key not in prog.funcs or prog.funcs[key].kind not in ("task", "workflow"):
        sys.stdout.write(format_diag(Diag(74, None, None, 0)))
        return EXIT_ERROR
    fc = prog.funcs[key]
    if fc.params:
        # implementer.md 9: the run target's arity must be 0.
        sys.stdout.write(format_diag(Diag(22, fc.file, None, 0)))
        return EXIT_ERROR
    vm = make_vm(root, prog, art, fn_ids)
    fn_id = fn_ids[key]
    try:
        result = vm.call(fn_id, [])
    except Trap as t:
        sys.stdout.write(format_diag(Diag(101, None, None, 0, trap=t.code)))
        return EXIT_TRAP
    finally:
        vm.observe.dump(root)
    if isinstance(result, Unit):
        return EXIT_OK
    try:
        text = jsonc.encode(art, result, art.funcs[fn_id].ret)
    except (ConvertFail, NotJson):
        sys.stdout.write(format_diag(Diag(97, fc.file, None, 0)))
        return EXIT_ERROR
    sys.stdout.write(text + "\n")
    return EXIT_OK


def cmd_test(root: str) -> int:
    from gopyt.vm import Trap

    prog, art, fn_ids = build(root)
    tests = [fc for fc in prog.funcs.values() if fc.kind == "test"]
    tests.sort(key=lambda fc: (fc.file.encode("utf-8"), fc.symbol.encode("utf-8")))
    vm = make_vm(root, prog, art, fn_ids)
    for fc in tests:
        try:
            vm.call(fn_ids[fc.key], [])
        except Trap as t:
            sys.stdout.write(format_diag(Diag(101, fc.file, None, 0, trap=t.code)))
            vm.observe.dump(root)
            return EXIT_TRAP
    vm.observe.dump(root)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.stderr.write("gopyt: missing command\n")
        return EXIT_ERROR
    cmd = argv[0]
    if cmd not in ("check", "fmt", "test", "run"):
        sys.stderr.write("gopyt: unknown command\n")
        return EXIT_ERROR
    if cmd == "run" and len(argv) != 2:
        sys.stderr.write("gopyt: run needs one module.path.task\n")
        return EXIT_ERROR
    if cmd != "run" and len(argv) != 1:
        sys.stderr.write("gopyt: unexpected argument\n")
        return EXIT_ERROR
    try:
        root = find_root(os.getcwd())
        if cmd == "fmt":
            return cmd_fmt(root)
        if cmd == "check":
            return cmd_check(root)
        if cmd == "run":
            return cmd_run(root, argv[1])
        return cmd_test(root)
    except CompileError as e:
        sys.stdout.write(format_diag(e.diag))
        return EXIT_ERROR
    except UnicodeDecodeError:
        sys.stdout.write(format_diag(Diag(3, None, 1, 0)))
        return EXIT_ERROR
    except RecursionError:
        sys.stdout.write(format_diag(Diag(1, None, None, 0)))
        return EXIT_ERROR
    except Exception:
        # S27: an implementation must not guess at unspecified behavior.
        sys.stdout.write(format_diag(Diag(1, None, None, 0)))
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
