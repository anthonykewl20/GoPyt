"""Reproduce architecture-review observations on the pinned baseline.

This is an observation script, not a passing regression gate. Unexpected
exceptions and documented limitations are retained as findings in its JSON.
All packages, credentials and storage are disposable local fixtures.
"""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gopyt.cli import build
from gopyt.testing import write_pkg
from gopyt.storage import Store, DIRECTORY, DATABASE
from gopyt.vm import VM


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--boundary-only', action='store_true', help='omit encryption controls when the security extra is absent')
    args = parser.parse_args()
    report = {'source_commit': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'python': sys.version, 'observations': []}
    def record(name, classification, **facts):
        report['observations'].append(dict(name=name, classification=classification, **facts))
    def finish():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as out: json.dump(report, out, indent=2); out.write('\n')
        print(json.dumps(report, indent=2))

    env = {k: v for k, v in os.environ.items() if not k.startswith('GOPYT_')}
    with patch.dict(os.environ, env, clear=True), tempfile.TemporaryDirectory() as temp:
        base = Path(temp).resolve()
        root = base / 'app'; root.mkdir()
        signatures = '''task read() -> str | NotFound | DbError
    effects { database.read }

task pause(ms: i64) -> unit
    effects { time }

task local() -> str | ModelError
    effects { model }
'''
        spec = 'module probe\n\nuse core.status { NotFound, DbError, ModelError }\n\n' + signatures
        impl = '''module probe

use core.status { NotFound, DbError, ModelError }
use store.db { get }
use core.time { sleep_ms }
use core.model { local }

task read() -> str | NotFound | DbError
    effects { database.read }
{
    return store.db.get("item")
}

task pause(ms: i64) -> unit
    effects { time }
{
    core.time.sleep_ms(ms)
    return unit
}

task local() -> str | ModelError
    effects { model }
{
    return core.model.local("probe", 1)
}
'''
        signatures_float = '\nfn large_float() -> f64\n'
        spec += signatures_float
        impl += signatures_float + '{\n    return ' + '9' * 400 + '.0\n}\n'
        write_pkg(str(root), {'spec/probe.gopyt': spec, 'impl/probe.gopyt': impl}, fmt=True)
        _, art, ids = build(str(root)); vm = VM(art, str(root))
        large_float = vm.call(ids['probe.large_float'], [])
        record('nonfinite_float_literal', 'observed behavior requiring an explicit numeric policy',
               literal_integer_digits=400, fractional_part='0',
               returned_type=type(large_float).__name__, is_infinite=math.isinf(large_float))
        # The actual compiled language path, with a clean missing-provider import.
        child = subprocess.run([sys.executable, '-I', '-c',
            'import sys,json,importlib.util;sys.path.insert(0,sys.argv[1]);'
            'from gopyt.cli import build;from gopyt.vm import VM;'
            'p,a,i=build(sys.argv[2]);v=VM(a,sys.argv[2]);'
            '\ntry: r=v.call(i["probe.local"],[]); print(json.dumps({"returned":type(r).__name__}))'
            '\nexcept Exception as e: print(json.dumps({"exception":type(e).__name__,"detail":str(e)}))',
            str(ROOT), str(root)], capture_output=True, text=True, timeout=20)
        if child.returncode: raise RuntimeError(child.stderr)
        record('optional_provider_absence', 'observed optional-provider boundary',
               actual=json.loads(child.stdout))

        Store(root).put('item', 'A')
        policy = base / 'policy.json'
        policy.write_text('[' * 12000 + '0' + ']' * 12000); policy.chmod(0o600)
        with patch.dict(os.environ, {'GOPYT_DB_POLICY_FILE': str(policy)}):
            try:
                result = vm.call(ids['probe.read'], [])
                actual = {'returned': vm.type_name(result.type_id)}
            except Exception as exc:
                actual = {'exception': type(exc).__name__, 'detail': str(exc)}
        record('deep_operator_policy', 'trusted configuration rejection; outcome depends on host Python',
               bytes=policy.stat().st_size, recursion_limit=sys.getrecursionlimit(), actual=actual,
               unchanged=Store(root).get('item') == 'A')

        for duration in (0, 2**63 - 1):
            try:
                result = vm.call(ids['probe.pause'], [duration])
                actual = {'returned': type(result).__name__}
            except Exception as exc:
                actual = {'exception': type(exc).__name__, 'detail': str(exc)}
            record('sleep_ms_' + str(duration), 'control' if duration == 0 else 'native range boundary defect',
                   actual=actual)

        # Deadline is observed before and after the blocking native, not inside it.
        from gopyt.guard import timed_call
        began = time.monotonic()
        try:
            timed_call(vm, ids['probe.pause'], [250], timeout=.01)
            outcome = 'returned'
        except Exception as exc: outcome = type(exc).__name__
        record('blocking_native_deadline', 'documented cancellation limitation',
               timeout_seconds=.01, sleep_seconds=.25,
               elapsed_seconds=time.monotonic() - began, outcome=outcome)

        store = Store(root); observed = store.get('item')
        store.put('item', 'B'); store.put('item', 'A')
        record('aba', 'documented value-CAS limitation',
               stale_value_accepted=store.compare_exchange('item', observed, 'C'))

        if args.boundary_only:
            finish()
            return

        key = base / 'key'; key.write_bytes(os.urandom(32)); key.chmod(0o600)
        encrypted_root = base / 'encrypted'; encrypted_root.mkdir()
        settings = {'GOPYT_SECURITY_PROFILE': 'strict', 'GOPYT_STORE_KEY_FILE': str(key),
                    'GOPYT_STORE_ID': 'review-disposable'}
        with patch.dict(os.environ, settings):
            encrypted = Store(encrypted_root); encrypted.put('item', 'old')
            path = encrypted_root / DIRECTORY / DATABASE
            old = path.read_bytes(); encrypted.put('item', 'new')
            path.write_bytes(old)
            record('authenticated_snapshot_replay', 'documented rollback limitation',
                   restored_value=Store(encrypted_root).get('item'))
            ring = base / 'ring.json'; new = os.urandom(32)
            ring.write_text(json.dumps({'version': 1, 'active': 'new',
                'keys': {'old': key.read_bytes().hex(), 'new': new.hex()}})); ring.chmod(0o600)
            rotation = {'GOPYT_STORE_KEY_FILE': '', 'GOPYT_STORE_KEYRING_FILE': str(ring)}
            with patch.dict(os.environ, rotation):
                encrypted.rekey()
                ring.write_text(json.dumps({'version': 1, 'active': 'new', 'keys': {'new': new.hex()}}))
                child = subprocess.run([sys.executable, '-c',
                    'import sys,json;sys.path.insert(0,sys.argv[1]);from gopyt.storage import Store;'
                    'print(json.dumps({"value":Store(sys.argv[2]).get("item")}))',
                    str(ROOT), str(encrypted_root)], capture_output=True, text=True, timeout=20)
                if child.returncode: raise RuntimeError(child.stderr)
                record('retired_key_fresh_process_read', 'positive control', actual=json.loads(child.stdout))

    finish()


if __name__ == '__main__': main()
