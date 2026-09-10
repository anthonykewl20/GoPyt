"""Isolated key-lifecycle acceptance drill; not an external custody certification."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from gopyt.storage import Store, StorageError, DIRECTORY, DATABASE
from gopyt.toolchain import FINGERPRINT
from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV
import sqlite3


def private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    files = list((ROOT/'gopyt').glob('*.py')) + [Path(__file__).resolve(), ROOT/'docs/key-lifecycle.md']
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    (args.output/'inputs.json').write_text(json.dumps(dict(runtime_sha256=FINGERPRINT.hex(),
        python=sys.version, source_sha256=hashes), indent=2)+'\n')
    events = []
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve()
        root = base/'app'; root.mkdir()
        live = base/'live'; live.mkdir(mode=0o700)
        escrow = base/'escrow'; escrow.mkdir(mode=0o700)
        anchor = base/'authority'; anchor.mkdir(mode=0o700)
        a, b = os.urandom(32), os.urandom(32)
        private(live/'key', a); private(escrow/'key-a', a); private(escrow/'key-b', b)
        expected = {'balance': '10', 'receipt': 'paid', 'owner': 'Ω'}
        env = {k:v for k,v in os.environ.items() if not k.startswith('GOPYT_')}
        env.update(GOPYT_SECURITY_PROFILE='strict', GOPYT_STORE_KEY_FILE=str(live/'key'),
                   GOPYT_STORE_ID='lifecycle-drill', GOPYT_STORE_ANCHOR_DIR=str(anchor))
        def fresh(reject=False):
            code = ('from gopyt.storage import Store,StorageError;import sys,json\n'
                    'try: value=Store(sys.argv[1]).get_many(["balance","receipt","owner"])\n'
                    'except StorageError: sys.exit(7)\n'
                    'assert value==["10","paid","Ω"]\n')
            result = subprocess.run([sys.executable, '-c', code, str(root)], cwd=ROOT,
                env=dict(os.environ), capture_output=True, timeout=20)
            assert result.returncode == (7 if reject else 0), result.stderr
            events.append({'fresh_process': 'rejected' if reject else 'restored',
                           'stdout': result.stdout.decode(), 'stderr': result.stderr.decode()})
        with patch.dict(os.environ, env, clear=True):
            store = Store(root); store.enroll_anchor()
            for key,value in expected.items(): store.put(key,value)
            path = root/DIRECTORY/DATABASE
            backup = path.read_bytes(); private(base/'backup', backup)
            assert store.get('balance') == '10'
            (live/'key').unlink()
            before = path.read_bytes(); record = (anchor/'record.json').read_bytes()
            try: store.get('balance')
            except StorageError: pass
            else: raise AssertionError('cached value bypassed missing key')
            fresh(reject=True)
            assert path.read_bytes() == before and (anchor/'record.json').read_bytes() == record
            private(live/'key', (escrow/'key-a').read_bytes()); fresh()
            private(live/'ring', json.dumps({'version':1,'active':'b','keys':{'a':a.hex(),'b':b.hex()}}).encode())
            os.environ.pop('GOPYT_STORE_KEY_FILE')
            os.environ['GOPYT_STORE_KEYRING_FILE'] = str(live/'ring')
            generation = Store(root).anchor_status()['generation']
            Store(root).fence_key(expected_generation=generation)
            private(live/'current', json.dumps({'version':1,'active':'b','keys':{'b':b.hex()}}).encode())
            with patch.dict(os.environ, {'GOPYT_STORE_KEYRING_FILE':str(live/'current')}): fresh()
            restored = Store(root).restore_anchor(backup,
                expected_generation=generation+1, reason='verified lifecycle backup drill')
            assert restored['generation'] == generation+2
            current = path.read_bytes()
            prefix = b'GOPYT-SIV1\0'
            plain = AESGCMSIV(b).decrypt(current[len(prefix):len(prefix)+12],
                current[len(prefix)+12:], prefix+b'lifecycle-drill')
            db = sqlite3.connect(':memory:')
            try:
                db.deserialize(plain)
                assert dict(db.execute('SELECT key,value FROM kv')) == expected
            finally: db.close()
            with patch.dict(os.environ, {'GOPYT_STORE_KEYRING_FILE':str(live/'current')}):
                fresh()
                (live/'current').unlink()
                fresh(reject=True)
                assert path.read_bytes() == current
                recovered = (escrow/'key-b').read_bytes()
                private(live/'current', json.dumps({'version':1,'active':'b','keys':{'b':recovered.hex()}}).encode())
                fresh()
        (args.output/'events.json').write_text(json.dumps(events, indent=2)+'\n')
        result = dict(status='passed', missing_live_key_cached_and_fresh_rejected=True,
            escrow_recovery_fresh_process=True, old_backup_republished_under_current_key=True,
            current_key_only_fresh_process=True, missing_current_key_rejected=True,
            independent_restored_rows=len(expected), scope='Isolated same-host custody simulation; not external escrow, power-loss or security certification.')
        (args.output/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        for path in args.output.rglob('*'):
            if path.is_file():
                contents=path.read_bytes()
                for key in (a,b):
                    assert all(value not in contents for value in (key,key.hex().encode(),base64.b64encode(key)))
        for name,digest in hashes.items(): assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest
        print(json.dumps(result))


if __name__ == '__main__': main()
