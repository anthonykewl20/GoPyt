"""Compiler-derived context and replayable source-bound receipts.

Developer tooling, deliberately separate from the locked four-command CLI.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import sys
import tempfile

from gopyt import ast_nodes as ast
from gopyt.check import CallRes, ParallelRes, check_package, load_package
from gopyt.diag import CompileError, format_diag
from gopyt.emit import emit_program
from gopyt.files import regular_file
from gopyt.fmt import fmt_item
from gopyt.gobyte import decode, encode
from gopyt.manifest import TOOLCHAIN, manifest_text, parse_manifest, validate_source_tree
from gopyt.transaction import guard
from gopyt.vm import Trap, VM

SCHEMA = 'gopyt.project-receipt.v1'


def digest(data: bytes) -> str:
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()


def engine_identity() -> dict:
    base = Path(__file__).resolve().parent
    files = {p.name: digest(p.read_bytes()) for p in sorted(base.glob('*.py'))
             if not p.name.startswith('test_')}
    return {'toolchain': TOOLCHAIN, 'files': files, 'sha256': digest(canonical(files)),
            'python': platform.python_version(), 'implementation': platform.python_implementation()}


def source_files(root: Path) -> dict[str, bytes]:
    validate_source_tree(str(root))
    names = ['gopyt.toml']
    if os.path.lexists(root / 'gopyt.lock'):
        names.append('gopyt.lock')
    for folder in ('spec', 'impl', 'test'):
        for path, dirs, files in os.walk(root / folder):
            dirs.sort()
            names.extend((Path(path) / name).relative_to(root).as_posix() for name in sorted(files))
    result = {}
    for name in sorted(names):
        with regular_file(str(root), name) as stream:
            result[name] = stream.read()
    return result


def capture_graph(root: Path) -> tuple[dict, dict]:
    """Copy actual compiler inputs; paths stay relative to the requesting package.

    Per-package advisory locks protect cooperating writers. This is not an atomic
    cross-package snapshot against uncoordinated writers. The compiler validates
    the copied graph and its root lock before reporting any success.
    """
    pending = [root]
    captures = {}
    while pending:
        path = pending.pop()
        rel = os.path.relpath(path, root).replace(os.sep, '/')
        if rel in captures:
            continue
        with guard(str(path)):
            man = parse_manifest(str(path))
            files = source_files(path)
            # The captured manifest must be the one used for traversal.
            with regular_file(str(path), 'gopyt.toml') as stream:
                if stream.read() != files['gopyt.toml'] or files['gopyt.toml'] != manifest_text(man).encode():
                    raise ValueError('manifest changed while capturing sources')
            captures[rel] = files
            pending.extend(Path(os.path.abspath(path / dep)) for dep in man.deps.values())
    inventories = {rel: {name: digest(raw) for name, raw in files.items()}
                   for rel, files in sorted(captures.items())}
    identity = {'packages': inventories, 'sha256': digest(canonical(inventories))}
    return captures, identity


def materialize(captures: dict, original_root: Path, temp: Path) -> Path:
    common = Path(os.path.commonpath([os.path.abspath(original_root / rel) for rel in captures]))
    copied_root = temp / original_root.relative_to(common)
    for rel, files in captures.items():
        target = temp / Path(os.path.abspath(original_root / rel)).relative_to(common)
        for name, raw in files.items():
            dest = target / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
    return copied_root


def without_comments(value):
    """Comments are accessible in source; they never become executable contracts."""
    value = copy.deepcopy(value)
    def visit(node):
        if isinstance(node, ast.Node):
            node.comments = []
            node.trailing = None
            for item in vars(node).values():
                visit(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                visit(item)
    visit(value)
    return value


def declaration_key(module: str, item) -> str:
    if isinstance(item, ast.FnDecl):
        name = item.sig.name
    elif isinstance(item, ast.ProvideDecl):
        # Canonical header captures generic arguments and has no implementation.
        name = fmt_item(without_comments(item)).splitlines()[0]
    elif isinstance(item, ast.HttpDecl):
        name = 'http'
    elif isinstance(item, ast.EgressDecl):
        name = 'egress'
    else:
        name = item.name
    return module + ':' + type(item).__name__ + ':' + name


def project_context(pkg, program) -> dict:
    modules = []
    declarations = []
    for group in (pkg.spec, pkg.dep_specs, pkg.impl, pkg.dep_impls, pkg.tests):
        for mod in group.values():
            modules.append({'module': mod.path, 'role': mod.role, 'file': mod.file,
                            'owner': pkg.module_owner[mod.path],
                            'imports': [{'module': use.path, 'names': use.names, 'line': use.line}
                                        for use in mod.uses]})
            if mod.role != 'spec':
                continue
            for item in mod.items:
                declarations.append({'id': declaration_key(mod.path, item), 'module': mod.path,
                                     'file': mod.file, 'line': item.line,
                                     'kind': type(item).__name__,
                                     'declaration': fmt_item(without_comments(item))})
    edges = set()
    for fc in program.funcs.values():
        for resolution in fc.res.values():
            if isinstance(resolution, CallRes):
                edges.add((fc.key, resolution.key, 'call'))
            elif isinstance(resolution, ParallelRes):
                for arm in resolution.arms:
                    edges.add((fc.key, arm.symbol, 'parallel_arm'))
    # HTTP dispatch is framework-owned and has no source CallRes.
    for module, verb, path, handler in program.routes:
        edges.add((module + '.serve', handler, 'http_dispatch'))
    functions = [{'id': fc.key, 'file': fc.file, 'kind': fc.kind,
                  'parameters': [{'name': name, 'type': ty.key} for name, ty in fc.params],
                  'returns': fc.ret.key, 'effects': sorted(fc.effects)}
                 for fc in program.funcs.values()]
    return {'modules': sorted(modules, key=lambda row: (row['file'], row['role'])),
            'public_declarations': sorted(declarations, key=lambda row: row['id']),
            'functions': sorted(functions, key=lambda row: row['id']),
            'resolved_edges': [{'caller': caller, 'callee': callee, 'kind': kind}
                               for caller, callee, kind in sorted(edges)],
            'packages': [{'name': name, 'version': version, 'path': path, 'digest': value}
                         for name, version, path, value in pkg.packages],
            'direct_dependencies': {name: sorted(deps) for name, deps in sorted(pkg.package_deps.items())},
            'egress': [list(row) for row in program.egress],
            'edge_scope': 'Compiler-resolved instantiated calls, parallel arms and HTTP dispatch. '
                          'Uninstantiated generic bodies have declarations but no concrete call edges.',
            'obligations': 'Compilation discharged required tests and rejected remaining open/unresolved holes. '
                           'This is not evidence that tests passed.'}


def create_receipt(root: Path, operation: str) -> dict:
    if operation not in ('context', 'check', 'test'):
        raise ValueError('unknown operation')
    root = Path(os.path.abspath(root))
    result = {'schema': SCHEMA, 'operation': operation, 'engine': engine_identity(),
              'source': None, 'context': None, 'artifact_sha256': None,
              'compile': {'status': 'not_run', 'exit_code': None, 'diagnostic': ''},
              'tests': {'status': 'not_run', 'cases': [], 'planned': []},
              'exit_code': 1, 'stdout': '', 'stderr': '',
              'execution_scope': 'Copied source graph; tests share one VM at a fresh temporary package root. '
                                 'External effects/environment are not captured or isolated.',
              'business_acceptance': 'not_evaluated'}
    stdout, stderr = io.StringIO(), io.StringIO()
    try:
        captures, result['source'] = capture_graph(root)
        with tempfile.TemporaryDirectory(prefix='gopyt-receipt-') as directory:
            staged = materialize(captures, root, Path(directory))
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                pkg = load_package(str(staged))
                prog = check_package(pkg)
                artifact, ids = emit_program(prog)
                bytecode = encode(artifact)
                artifact = decode(bytecode)
                result['compile'] = {'status': 'passed', 'exit_code': 0, 'diagnostic': ''}
                result['artifact_sha256'] = digest(bytecode)
                result['context'] = project_context(pkg, prog)
                result['exit_code'] = 0
                if operation == 'test':
                    tests = sorted((fc for fc in prog.funcs.values() if fc.kind == 'test'),
                                   key=lambda fc: (fc.file.encode(), fc.symbol.encode()))
                    result['tests']['planned'] = [fc.key for fc in tests]
                    result['tests']['status'] = 'running' if tests else 'no_tests'
                    with VM(artifact, root=str(staged)) as vm:
                        for fc in tests:
                            entry = {'name': fc.key, 'file': fc.file, 'status': 'passed', 'trap': None}
                            try:
                                vm.call(ids[fc.key], [])
                            except Trap as exc:
                                entry.update(status='failed', trap=exc.code)
                                result['tests']['status'] = 'failed'
                                result['exit_code'] = 2
                            except Exception as exc:
                                entry.update(status='error', error={'type': type(exc).__name__, 'message': str(exc)})
                                result['tests']['status'] = 'error'
                                result['exit_code'] = 1
                            finally:
                                vm.heap.release_result()
                            result['tests']['cases'].append(entry)
                            if entry['status'] != 'passed':
                                break
                    if result['tests']['status'] == 'running':
                        result['tests']['status'] = 'passed'
                _, after = capture_graph(staged)
                if after != result['source']:
                    result['exit_code'] = 1
                    result['source_changed_during_execution'] = True
    except CompileError as exc:
        if result['compile']['status'] == 'passed':
            result['error'] = {'type': 'CompileError', 'message': format_diag(exc.diag)}
            if result['tests']['status'] == 'running':
                result['tests']['status'] = 'error'
        else:
            result['compile'] = {'status': 'failed', 'exit_code': 1, 'diagnostic': format_diag(exc.diag)}
        result['exit_code'] = 1
    except Exception as exc:
        if result['tests']['status'] == 'running':
            result['tests']['status'] = 'error'
        result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        result['exit_code'] = 1
    result['stdout'], result['stderr'] = stdout.getvalue(), stderr.getvalue()
    result['receipt_sha256'] = digest(canonical(result))
    return result


def validate_receipt(record: dict) -> None:
    if not isinstance(record, dict) or record.get('schema') != SCHEMA:
        raise ValueError('unsupported receipt schema')
    unsigned = dict(record)
    claimed = unsigned.pop('receipt_sha256', None)
    if claimed != digest(canonical(unsigned)):
        raise ValueError('receipt content digest mismatch')
    if record.get('operation') not in ('context', 'check', 'test'):
        raise ValueError('invalid operation')


def verify_receipt(root: Path, record: dict) -> dict:
    """Replay current source, without claiming authentication of past execution."""
    validate_receipt(record)
    current = create_receipt(root, record['operation'])
    keys = ('engine', 'source', 'context', 'artifact_sha256', 'compile', 'tests', 'exit_code',
            'source_changed_during_execution', 'error')
    differences = [key for key in keys if current.get(key) != record.get(key)]
    # Missing identity cannot establish a source-bound replay, even if both fail.
    if record.get('source') is None:
        differences.append('missing_source_identity')
    return {'schema': 'gopyt.project-replay.v1', 'matches': not differences,
            'differences': differences, 'replayed_exit_code': current['exit_code'],
            'receipt_sha256': record['receipt_sha256'], 'replay': current,
            'meaning': 'Replay of the recorded operation against current source; failures may match. '
                       'Not authentication of historical execution or business acceptance. '
                       'stdout/stderr are retained but excluded from matching (effects may vary).'}


def diff_context(before: dict, after: dict) -> dict:
    for record in (before, after):
        validate_receipt(record)
        if record.get('compile', {}).get('status') != 'passed' or record.get('context') is None:
            raise ValueError('context diff requires two successful compilations')
    def entries(record):
        return {row['id']: row for row in record['context']['public_declarations']}
    old, new = entries(before), entries(after)
    return {'schema': 'gopyt.public-diff.v1',
            'before_source': before['source']['sha256'], 'after_source': after['source']['sha256'],
            'added': [new[key] for key in sorted(new.keys() - old.keys())],
            'removed': [old[key] for key in sorted(old.keys() - new.keys())],
            'changed': [{'before': old[key], 'after': new[key]}
                        for key in sorted(old.keys() & new.keys())
                        if old[key]['declaration'] != new[key]['declaration']],
            'meaning': 'Declaration text changes, not an inferred compatibility or business-policy verdict.'}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('context', 'check', 'test'):
        cmd = sub.add_parser(name)
        cmd.add_argument('root', type=Path)
        cmd.add_argument('--output', type=Path, required=True)
    cmd = sub.add_parser('verify')
    cmd.add_argument('root', type=Path)
    cmd.add_argument('receipt', type=Path)
    cmd.add_argument('--output', type=Path, required=True)
    cmd = sub.add_parser('diff')
    cmd.add_argument('before', type=Path)
    cmd.add_argument('after', type=Path)
    cmd.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    # Retain failures and old receipts: output paths must be new.
    try:
        with args.output.open('x', encoding='utf-8') as stream:
            if args.command == 'verify':
                result = verify_receipt(args.root, json.loads(args.receipt.read_text()))
                code = 0 if result['matches'] else 1
            elif args.command == 'diff':
                result = diff_context(json.loads(args.before.read_text()), json.loads(args.after.read_text()))
                code = 0
            else:
                result = create_receipt(args.root, args.command)
                code = result['exit_code']
            json.dump(result, stream, indent=2, sort_keys=True)
            stream.write('\n')
        return code
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'project context: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
