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
