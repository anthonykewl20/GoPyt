"""Reproduce the absence of explicit plaintext migration on the frozen runtime."""
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

with tempfile.TemporaryDirectory() as temporary:
    base = Path(temporary).resolve()
    root = base / 'app'
    root.mkdir()
    key = base / 'key'
    key.write_bytes(os.urandom(32))
    key.chmod(0o600)
    env = {'GOPYT_SECURITY_PROFILE': 'development', 'GOPYT_STORE_KEY_FILE': '',
           'GOPYT_STORE_KEYRING_FILE': '', 'GOPYT_STORE_ANCHOR_DIR': '',
           'GOPYT_STORE_ID': 'migration-probe'}
    with patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith('GOPYT_')} | env, clear=True):
        Store(root).put('balance', '10')
        path = root / DIRECTORY / DATABASE
        original = path.read_bytes()
        with patch.dict(os.environ, {'GOPYT_SECURITY_PROFILE': 'strict',
                                     'GOPYT_STORE_KEY_FILE': str(key)}):
            rejected = []
            for name, operation in [('read', lambda: Store(root).get('balance')),
                                    ('rekey', lambda: Store(root).rekey())]:
                try:
                    operation()
                except StorageError:
                    rejected.append(name)
            result = subprocess.run([sys.executable, '-m', 'gopyt.store_admin',
                'migrate', '--root', str(root)], cwd=ROOT, capture_output=True, text=True)
        assert path.read_bytes() == original
    report = {'runtime_sha256': FINGERPRINT.hex(),
              'probe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'python': sys.version, 'encrypted_mode_rejected': rejected,
              'migration_cli_exit': result.returncode,
              'migration_command_absent': "invalid choice: 'migrate'" in result.stderr,
              'plaintext_unchanged': True}
    assert rejected == ['read', 'rekey'] and report['migration_command_absent']
    Path(sys.argv[1]).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
