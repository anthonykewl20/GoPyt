"""Pinned acceptance for a small, dependency-free pure GoPyT policy package.

The caller supplies a trusted bundle hash OUTSIDE the candidate tree. Candidate
files are data, never Python or shell commands. This is not an OS sandbox or a
proof of correctness. Protect this executor and its caller in CI separately.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time

MAX_FILES = 64
MAX_BYTES = 2_000_000


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def unique_json(raw):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def engine_digest():
    root = Path(__file__).parent
    return digest(canonical({'python': sys.version, 'sources':
        {p.name: digest(p.read_bytes()) for p in sorted(root.glob('*.py'))
         if not p.name.startswith('test_')}}))


def snapshot(root):
    """Read bounded regular files through directory FDs; never follow symlinks."""
    result = {}
    size = 0
    entries = 0
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    def walk(fd, prefix=''):
        nonlocal size, entries
        names = os.listdir(fd)
        entries += len(names)
        if entries > MAX_FILES * 2:
            raise ValueError('candidate entry count exceeds limit')
        for name in sorted(names):
            if name in ('.', '..') or '/' in name:
                raise ValueError('unsafe path')
            child = os.open(name, flags, dir_fd=fd)
            try:
                info = os.fstat(child)
                rel = prefix + name
                if stat.S_ISDIR(info.st_mode):
                    if prefix.count('/') >= 8:
                        raise ValueError('directory depth exceeds limit')
                    walk(child, rel + '/')
                elif stat.S_ISREG(info.st_mode):
                    if len(result) >= MAX_FILES or info.st_size > MAX_BYTES:
                        raise ValueError('candidate size exceeds limit')
                    chunks = []
                    while True:
                        chunk = os.read(child, min(65536, MAX_BYTES - size + 1))
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise ValueError('candidate size exceeds limit')
                        chunks.append(chunk)
                    result[rel] = b''.join(chunks)
                else:
                    raise ValueError('only regular files and directories allowed')
            finally:
                os.close(child)
    fd = os.open(root, flags | os.O_DIRECTORY)
    try:
        walk(fd)
    finally:
        os.close(fd)
    return result


def materialize(files, root):
    for rel, raw in files.items():
        path = Path(root, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def prepare_bundle(package, mutable, cases, obligations):
    """Prepare reviewable bytes, NOT an approval. Operator must trust/pin them."""
    files = snapshot(package)
    if not mutable or not set(mutable) <= files.keys():
        raise ValueError('mutable files must exist')
    if any(not p.startswith('impl/') or not p.endswith('.gopyt') for p in mutable):
        raise ValueError('only implementation sources may change')
    # No candidate-supplied dependencies, hooks, native code, or generated files.
    import tomllib
    manifest = tomllib.loads(files['gopyt.toml'].decode())
    if set(manifest) != {'name', 'version'}:
        raise ValueError('only name/version manifest supported')
    for rel in files:
        if rel not in ('gopyt.toml', 'gopyt.lock') and not (
            rel.startswith(('spec/', 'impl/', 'test/')) and rel.endswith('.gopyt')
        ):
            raise ValueError('unsupported package file: ' + rel)
    if not cases or not obligations:
        raise ValueError('acceptance cases and obligations required')
    engine = engine_digest()
    bundle = {'schema': 'gopyt.guard.bundle.v2', 'engine_sha256': engine,
              'files': {p: digest(b) for p, b in files.items()},
              'mutable': sorted(mutable), 'cases': cases, 'obligations': obligations,
              'contract_dependencies': {}}
    # Reject invalid or oversized policies before compiler work.
    validate_bundle(canonical(bundle))
    with tempfile.TemporaryDirectory(prefix='gopyt-rule-baseline-') as temp:
        materialize(files, temp)
        program = _check_policy(temp)
        validate_case_signatures(program, cases)
        dependencies = _contract_dependencies(program)
    if engine != engine_digest():
        raise ValueError('engine changed during preparation')
    bundle['contract_dependencies'] = dependencies
    raw = canonical(bundle)
    validate_bundle(raw)
    return raw


def validate_bundle(raw):
    """A trusted hash authenticates bytes; it does not make their schema valid."""
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise ValueError('bundle must be bounded bytes')
    bundle = unique_json(raw)
    required = {'schema', 'engine_sha256', 'files', 'mutable', 'cases', 'obligations', 'contract_dependencies'}
    optional = {'adapter_sha256', 'acceptance_source_sha256'}
    if type(bundle) is not dict or not required <= bundle.keys() or bundle.keys() - required - optional:
        raise ValueError('invalid bundle fields')
    if bundle['schema'] != 'gopyt.guard.bundle.v2':
        raise ValueError('unsupported bundle schema')
    def hash_value(value):
        return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None
    if any(not hash_value(bundle[k]) for k in {'engine_sha256'} | (optional & bundle.keys())):
        raise ValueError('invalid bundle digest')
    dependencies = bundle['contract_dependencies']
    if type(dependencies) is not dict or any(type(k) is not str or not hash_value(v) for k,v in dependencies.items()):
        raise ValueError('invalid contract dependency binding')
    files = bundle['files']
    if type(files) is not dict or not 1 <= len(files) <= MAX_FILES:
        raise ValueError('invalid bundle inventory')
    if not {'gopyt.toml', 'gopyt.lock'} <= files.keys():
        raise ValueError('manifest and lock required')
    for path, value in files.items():
        if (not hash_value(value) or type(path) is not str
                or any(part in ('', '.', '..') for part in path.split('/'))
                or chr(92) in path or chr(0) in path
                or (path not in ('gopyt.toml', 'gopyt.lock') and not
                    (path.startswith(('spec/', 'impl/', 'test/')) and path.endswith('.gopyt')))):
            raise ValueError('invalid inventory path or digest')
    mutable = bundle['mutable']
    if (type(mutable) is not list or not mutable or any(type(p) is not str for p in mutable)
            or len(set(mutable)) != len(mutable) or not set(mutable) <= files.keys()
            or any(not p.startswith('impl/') or not p.endswith('.gopyt') for p in mutable)):
        raise ValueError('only existing implementation files may be mutable')
    obligations = bundle['obligations']
    if (type(obligations) is not dict or not obligations or
            any(not key or type(value) is not str or not value.strip() for key,value in obligations.items())):
        raise ValueError('nonempty obligations required')
    cases = bundle['cases']
    if type(cases) is not list or not 1 <= len(cases) <= 10000:
        raise ValueError('one to 10000 acceptance cases required')
    positive = False
    for case in cases:
        if type(case) is not dict or set(case) not in ({'symbol','args','expected'}, {'symbol','args','trap'}):
            raise ValueError('case requires exactly one expected outcome')
        if type(case['symbol']) is not str or not re.fullmatch(r'[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+', case['symbol']):
            raise ValueError('invalid case symbol')
        def scalar(v):
            return type(v) is bool or (type(v) is int and -(2**63) <= v < 2**63)
        if type(case['args']) is not list or len(case['args']) > 64 or not all(scalar(v) for v in case['args']):
            raise ValueError('case arguments must be bounded i64/bool scalars')
        if 'expected' in case:
            if not scalar(case['expected']):
                raise ValueError('case result must be i64/bool scalar')
            positive = True
        elif type(case['trap']) is not int or not 1 <= case['trap'] <= 255:
            raise ValueError('invalid expected trap')
    if not positive:
        raise ValueError('at least one successful-return case required')
    return bundle


def inspect_candidate(bundle_raw, expected_sha256, candidate):
    if digest(bundle_raw) != expected_sha256:
        raise ValueError('trusted bundle hash mismatch')
    bundle = validate_bundle(bundle_raw)
    if bundle['engine_sha256'] != engine_digest():
        raise ValueError('trusted engine hash mismatch')
    files = snapshot(candidate)
    if files.keys() != bundle['files'].keys():
        raise ValueError('candidate file inventory changed')
    import tomllib
    if set(tomllib.loads(files['gopyt.toml'].decode())) != {'name', 'version'}:
        raise ValueError('only name/version manifest supported')
    changed = [p for p, raw in files.items() if digest(raw) != bundle['files'][p]]
    protected = sorted(set(changed) - set(bundle['mutable']) - {'gopyt.lock'})
    if protected:
        raise ValueError('protected files changed: ' + ', '.join(protected))
    return bundle, files, sorted(changed)


def _check_policy(root):
    from gopyt.check import check_package, load_package
    from gopyt.testing import write_lock
    write_lock(str(root))
    return check_package(load_package(str(root)))


def contract_dependencies(root):
    return _contract_dependencies(_check_policy(root))


def _contract_dependencies(program):
    """Bind the meaning of contract calls, including transitive helper bodies.

    Use compiler-resolved instantiated calls, not text search or name guessing.
    The engine pin covers stdlib natives. Application helper ASTs and resolved
    call targets are protected; comments and source locations are not semantic.
    """
    from dataclasses import fields
    from gopyt.ast_nodes import Node
    from gopyt.check import CallRes, _contract_shape
    def calls(fn, node):
        if isinstance(node, Node):
            resolved = fn.res.get(id(node))
            if isinstance(resolved, CallRes):
                yield resolved.key
            for field in fields(node):
                yield from calls(fn, getattr(node, field.name))
        elif isinstance(node, (list, tuple)):
            for item in node:
                yield from calls(fn, item)
    pending = [key for fn in program.funcs.values() for key in calls(fn, fn.contracts)]
    seen = set()
    bound = {}
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        if key not in program.funcs:
            raise ValueError('unresolved contract dependency: ' + key)
        fn = program.funcs[key]
        if fn.body is None:
            continue
        targets = sorted({res.key for res in fn.res.values() if isinstance(res, CallRes)})
        bound[key] = digest(canonical({'signature': {
                                           'kind': fn.kind,
                                           'params': [(name, ty.key) for name, ty in fn.params],
                                           'return': fn.ret.key,
                                           'effects': sorted(fn.effects)},
                                       'body': _contract_shape(fn.body),
                                       'contracts': _contract_shape(fn.contracts),
                                       'calls': targets}))
        pending.extend(targets)
    return dict(sorted(bound.items()))


def _compile_policy(root):
    from gopyt.testing import write_lock
    from gopyt.cli import build, make_vm
    write_lock(str(root))  # Derived lock only, inside the captured disposable tree.
    program, artifact, ids = build(str(root))
    for fn in program.funcs.values():
        if fn.body is not None and (fn.kind != 'fn' or fn.effects):
            raise ValueError('only pure functions allowed')
    return program, make_vm(str(root), program, artifact, ids), ids


def compile_policy(root):
    _, vm, ids = _compile_policy(root)
    return vm, ids


def validate_case_signatures(program, cases):
    """JSON scalars must form valid calls, including cases expecting a trap."""
    from gopyt.types import I64, BOOL
    scalar_types = {I64: int, BOOL: bool}
    for index, case in enumerate(cases):
        def refuse(reason):
            raise ValueError(f'acceptance signature at case {index}: {reason}')
        if type(case) is not dict or type(case.get('symbol')) is not str:
            refuse('invalid case')
        fn = program.funcs.get(case['symbol'])
        if fn is None or fn.body is None or fn.kind != 'fn' or fn.effects:
            refuse('expected an application pure function')
        args = case.get('args')
        if type(args) is not list or len(args) != len(fn.params):
            refuse('argument count mismatch')
        if fn.ret not in scalar_types:
            refuse('unsupported return type')
        for value, (_, ty) in zip(args, fn.params):
            if ty not in scalar_types or type(value) is not scalar_types[ty]:
                refuse('argument type mismatch or unsupported parameter')
        if 'expected' in case and type(case['expected']) is not scalar_types[fn.ret]:
            refuse('expected result type mismatch')


class _Deadline:
    """VM cancellation token without a thread allocation for every test case."""
    def __init__(self, timeout):
        self.expires = time.monotonic() + timeout

    def is_set(self):
        return time.monotonic() >= self.expires


def timed_call(vm, fn, args, timeout=1.0):
    cancel = _Deadline(timeout)
    previous = vm.cancels
    vm.cancels = previous + (cancel,)
    try:
        return vm.call(fn, list(args))
    finally:
        vm.cancels = previous


def _worker(root, cases, expected_dependencies=None):
    if sys.platform.startswith('linux'):
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    from gopyt.vm import Trap
    program, vm, ids = _compile_policy(root)
    actual_dependencies = _contract_dependencies(program)
    if expected_dependencies is None or actual_dependencies != expected_dependencies:
        raise ValueError('contract helper meaning changed; operator review required')
    validate_case_signatures(program, cases)
    passed = 0
    failures = []
    for index, case in enumerate(cases):
        try:
            actual = timed_call(vm, ids[case['symbol']], case['args'])
            ok = ('expected' in case and type(actual) is type(case['expected'])
                  and actual == case['expected'])
            detail = {'actual': actual}
        except Trap as exc:
            ok = case.get('trap') == exc.code
            detail = {'trap': exc.code}
        except Exception as exc:
            ok = False
            detail = {'error': type(exc).__name__}
        passed += ok
        if not ok and len(failures) < 20:
            failures.append({'index': index, **detail})
    return {'accepted': passed == len(cases), 'passed': passed,
            'total': len(cases), 'failures': failures}


def evaluate(bundle_raw, expected_sha256, candidate, timeout=60):
    receipt = {'schema': 'gopyt.guard.receipt.v1', 'accepted': False,
               'bundle_sha256': expected_sha256, 'engine_sha256': engine_digest(),
               'limitations': ['Finite acceptance cases, not a proof.',
                              'Listed obligations are declarations; this gate executes only the supplied cases, not HTTP or database tests.',
                              'Caller, bundle pin, and executor must be protected outside candidate authority.',
                              'No guarantee about external payment processors or undisclosed policies.']}
    try:
        bundle, files, changed = inspect_candidate(bundle_raw, expected_sha256, candidate)
        receipt.update(candidate_sha256=digest(canonical({p: digest(b) for p, b in files.items()})),
                       changed_files=changed, obligations=bundle['obligations'],
                       protected_contract_helpers=sorted(bundle['contract_dependencies']))
        with tempfile.TemporaryDirectory(prefix='gopyt-guard-') as temp:
            root = Path(temp, 'candidate')
            root.mkdir()
            materialize(files, root)
            cases = Path(temp, 'cases.json')
            cases.write_bytes(canonical({'cases': bundle['cases'], 'dependencies': bundle['contract_dependencies']}))
            # Isolated import search: candidate cwd/PYTHONPATH/site hooks cannot replace gopyt.
            script = ('import sys,json;sys.path.insert(0,sys.argv[1]);'
                      'from gopyt.guard import _worker;'
                      'data=json.load(open(sys.argv[3]));'
                      'print(json.dumps(_worker(sys.argv[2],data["cases"],data["dependencies"])))')
            child = subprocess.run([sys.executable, '-I', '-c', script,
                                    str(Path(__file__).resolve().parent.parent), str(root), str(cases)],
                                   cwd=temp, capture_output=True, text=True, timeout=timeout)
            if child.returncode:
                receipt['error'] = 'compile/executor failure'
                receipt['detail'] = child.stderr[-2000:]
            else:
                receipt.update(json.loads(child.stdout))
        # Detect accidental edits to the trusted toolchain during evaluation.
        if receipt['engine_sha256'] != engine_digest():
            receipt.update(accepted=False, error='engine changed during evaluation')
    except Exception as exc:
        receipt.update(accepted=False, error=type(exc).__name__ + ': ' + str(exc))
    return receipt


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'prepare':
        parser = argparse.ArgumentParser(description='Prepare a reviewable policy bundle; this does not grant approval.')
        parser.add_argument('--candidate', default='.')
        parser.add_argument('--policy', required=True, help='JSON with mutable, cases and obligations; review independently')
        parser.add_argument('--out', required=True)
        args = parser.parse_args(argv[1:])
        try:
            with open(args.policy, 'rb') as policy_file:
                policy_raw = policy_file.read(MAX_BYTES + 1)
            if len(policy_raw) > MAX_BYTES:
                raise ValueError('preparation policy exceeds byte limit')
            policy = unique_json(policy_raw)
            if type(policy) is not dict or set(policy) != {'mutable','cases','obligations'}:
                raise ValueError('policy requires mutable, cases and obligations')
            raw = prepare_bundle(args.candidate, policy['mutable'], policy['cases'], policy['obligations'])
            with open(args.out, 'xb') as handle:
                handle.write(raw)
            print(digest(raw))
            return 0
        except (ValueError, OSError, TypeError) as exc:
            print('guard prepare: ' + str(exc), file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--expected-sha256', required=True,
                        help='Operator-controlled pin; never read this from candidate output')
    parser.add_argument('--candidate', default='.')
    parser.add_argument('--receipt', required=True)
    args = parser.parse_args(argv)
    result = evaluate(Path(args.bundle).read_bytes(), args.expected_sha256, args.candidate)
    with open(args.receipt, 'xb') as handle:
        handle.write(canonical(result) + b'\n')
    print(json.dumps(result, indent=2))
    return 0 if result['accepted'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
