"""Exact source compatibility through fresh compiler/runtime processes."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from gopyt import gobyte
from gopyt.cli import build
from gopyt.diag import CompileError
from gopyt.testing import write_pkg
from gopyt.test_vm import module
from gopyt.toolchain import FINGERPRINT, TOOLCHAIN, source_fingerprint
from gopyt.vm import VM


class ToolchainCompatibility(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / 'app'
        self.root.mkdir()
        write_pkg(str(self.root), module('fn main() -> i64\n',
                                        'fn main() -> i64\n{\n    return 7\n}\n'), fmt=True)
        _, self.art, self.ids = build(str(self.root))
        self.data = gobyte.encode(self.art)

    def test_header_binds_source_and_rejects_old_and_unknown_versions(self):
        self.assertEqual(self.data[:6], b'GPYT\x03\x00')
        self.assertEqual(self.data[6:38], FINGERPRINT)
        self.assertEqual(gobyte.encode(gobyte.decode(self.data)), self.data)
        for version in (1, 2, 4, 255):
            changed = self.data[:4] + bytes([version]) + self.data[5:]
            with self.subTest(version=version), self.assertRaises(CompileError):
                gobyte.decode(changed)
        changed = self.data[:6] + bytes(x ^ 255 for x in FINGERPRINT) + self.data[38:]
        with self.assertRaises(CompileError):
            gobyte.decode(changed)
        self.assertIn(TOOLCHAIN, (self.root / 'gopyt.lock').read_text())

    def test_embedding_cannot_accidentally_use_mismatched_artifact(self):
        self.art.toolchain = b'\x00' * 32
        for operation in (gobyte.encode, gobyte.validate, VM):
            with self.subTest(operation=operation.__name__), self.assertRaises(CompileError) as error:
                operation(self.art)
            self.assertEqual(error.exception.diag.code, 100)

    def test_source_copy_matches_and_runtime_change_requires_rebuild(self):
        donor = Path(gobyte.__file__).parent
        clone = self.base / 'alternate' / 'gopyt'
        shutil.copytree(donor, clone, ignore=shutil.ignore_patterns('test_*', '__pycache__'))
        self.assertEqual(source_fingerprint(clone), FINGERPRINT)
        (clone / 'test_ignored.py').write_text('test fixture, not shipped runtime')
        self.assertEqual(source_fingerprint(clone), FINGERPRINT)
        with (clone / 'ops.py').open('a') as stream:
            stream.write('\n# Deliberately distinct toolchain identity.\n')
        self.assertNotEqual(source_fingerprint(clone), FINGERPRINT)
        artifact = self.base / 'original.gobyte'
        artifact.write_bytes(self.data)
        script = '''import sys,json
sys.path.insert(0,sys.argv[1])
from pathlib import Path
from gopyt import gobyte
from gopyt.cli import build
from gopyt.diag import CompileError
from gopyt.testing import write_pkg
from gopyt.vm import VM
root=Path(sys.argv[2]); oldlock=(root/'gopyt.lock').read_bytes()
try: gobyte.decode(Path(sys.argv[3]).read_bytes())
except CompileError as e: assert e.diag.code==100
else: raise AssertionError('accepted old artifact')
try: build(str(root))
except CompileError as e: assert e.diag.code==40
else: raise AssertionError('accepted old lock')
assert (root/'gopyt.lock').read_bytes()==oldlock
# The test operator explicitly reviews and writes the new computed lock.
from gopyt.manifest import lock_text
from gopyt.check import load_package
'''
        # Use the same existing package loader used by the standard test helper.
        helper = Path(donor, 'testing.py').read_text()
        self.assertIn('load_package', helper)
        script += '''pkg=load_package(str(root))
(root/'gopyt.lock').write_text(lock_text(pkg.packages))
_,art,ids=build(str(root))
assert VM(art,str(root)).call(ids['demo.main'],[])==7
Path(sys.argv[4]).write_bytes(gobyte.encode(art))
print(json.dumps({'old_artifact':100,'old_lock':40,'rebuilt_result':7}))
'''
        rebuilt = self.base / 'rebuilt.gobyte'
        result = subprocess.run([sys.executable, '-I', '-c', script, str(clone.parent),
                                 str(self.root), str(artifact), str(rebuilt)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['rebuilt_result'], 7)
        with self.assertRaises(CompileError):
            gobyte.decode(rebuilt.read_bytes())
        # Rollback uses the original artifact with its original runtime.
        self.assertEqual(VM(gobyte.decode(self.data), str(self.root)).call(self.ids['demo.main'], []), 7)
