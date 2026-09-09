"""Negative controls for wheel contents and RECORD integrity checks."""
import base64
import csv
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from tools.reproducible_wheel import inspect_wheel


class WheelIntegrity(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'gopyt.whl'
        self.expected = {'gopyt/demo.py': b'return_value = 42\n'}

    def wheel(self, payload=None, *, bad_hash=False, duplicate=False):
        payload = dict(self.expected if payload is None else payload)
        record = 'gopyt-0.1.0.dist-info/RECORD'
        payload.setdefault('gopyt-0.1.0.dist-info/METADATA', b'Name: gopyt\nVersion: 0.1.0\n')
        payload.setdefault('gopyt-0.1.0.dist-info/WHEEL', b'Wheel-Version: 1.0\nTag: py3-none-any\n')
        stream = io.StringIO()
        rows = csv.writer(stream)
        for name, data in payload.items():
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
            rows.writerow([name, 'sha256=' + ('wrong' if bad_hash else digest), str(len(data))])
        rows.writerow([record, '', ''])
        with zipfile.ZipFile(self.path, 'w') as archive:
            for name, data in payload.items():
                archive.writestr(name, data)
            archive.writestr(record, stream.getvalue())
            if duplicate:
                archive.writestr(record, stream.getvalue())

    def test_valid_wheel_matches_source(self):
        self.wheel()
        result = inspect_wheel(self.path, self.expected)
        self.assertEqual(result['files']['gopyt/demo.py'], hashlib.sha256(self.expected['gopyt/demo.py']).hexdigest())

    def test_modified_source_even_with_updated_record_is_rejected(self):
        self.wheel({'gopyt/demo.py': b'return_value = 0\n'})
        with self.assertRaisesRegex(ValueError, 'differs from source'):
            inspect_wheel(self.path, self.expected)

    def test_record_tampering_is_rejected(self):
        self.wheel(bad_hash=True)
        with self.assertRaisesRegex(ValueError, 'hash/size mismatch'):
            inspect_wheel(self.path, self.expected)

    def test_extra_payload_and_missing_source_are_rejected(self):
        for payload in ({**self.expected, 'gopyt/extra.py': b'pass'},
                        {**self.expected, 'gopyt-0.1.0.dist-info/extra.py': b'pass'}, {}):
            with self.subTest(payload=list(payload)):
                self.wheel(payload)
                with self.assertRaises(ValueError):
                    inspect_wheel(self.path, self.expected)

    def test_duplicate_and_oversized_inventory_are_rejected(self):
        with self.assertWarns(UserWarning):
            self.wheel(duplicate=True)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            inspect_wheel(self.path, self.expected)
        self.wheel({**self.expected, 'gopyt-0.1.0.dist-info/METADATA': b'x' * 1048577})
        with self.assertRaisesRegex(ValueError, 'budget'):
            inspect_wheel(self.path, self.expected)


class PackagingInputIntegration(unittest.TestCase):
    def test_hook_is_applied_and_caller_umask_does_not_change_wheel(self):
        import importlib.metadata
        import json
        import subprocess
        import sys
        from email.parser import BytesParser

        versions = {name: importlib.metadata.version(name) for name in ('pip', 'setuptools')}
        if versions != {'pip': '26.2.1', 'setuptools': '82.0.1'}:
            self.skipTest('integration requires reviewed requirements/build.txt')
        script = Path(__file__).resolve().parents[1] / 'tools/reproducible_wheel.py'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            (source / 'gopyt').mkdir(parents=True)
            (source / 'gopyt/__init__.py').write_text('VALUE = 42\n')
            (source / 'README.md').write_text('packaging integration fixture\n')
            (source / 'pyproject.toml').write_text(
                '[build-system]\nrequires = ["setuptools==82.0.1"]\n'
                'build-backend = "setuptools.build_meta"\n'
                '[project]\nname = "gopyt"\nversion = "0.1.0"\n'
                'dynamic = ["description"]\n')
            hook = 'from setuptools import setup\nsetup(description="HOOK_EXECUTED")\n'
            (source / 'setup.py').write_text(hook)
            hashes = []
            for index, mask in enumerate((0o002, 0o077)):
                output = root / str(index)
                result = subprocess.run([sys.executable, str(script), '--source', str(source),
                    '--output', str(output), '--epoch', '1788964852'],
                    capture_output=True, text=True, umask=mask, timeout=120)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                report = json.loads((output / 'report.json').read_text())
                self.assertEqual(report['source_sha256']['setup.py'],
                                 hashlib.sha256(hook.encode()).hexdigest())
                with zipfile.ZipFile(next((output / '0').glob('*.whl'))) as wheel:
                    metadata = BytesParser().parsebytes(wheel.read('gopyt-0.1.0.dist-info/METADATA'))
                    self.assertEqual(metadata['Summary'], 'HOOK_EXECUTED')
                hashes.append(report['builds'][0]['sha256'])
            self.assertEqual(hashes[0], hashes[1])
