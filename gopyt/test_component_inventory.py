import base64
import hashlib
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from tools.component_inventory import inventory


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.files = []
        from email.message import Message
        self.metadata = Message()
        self.metadata['Name'] = 'fixture'
        self.metadata['Version'] = '1'
        self.dist = types.SimpleNamespace(files=self.files, metadata=self.metadata,
            version='1', locate_file=lambda item: self.root / str(item))

    def add(self, name, data):
        from importlib.metadata import PackagePath, FileHash
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        item = PackagePath(name)
        item.hash = FileHash('sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('='))
        item.size = len(data)
        self.files.append(item)
        return path

    def collect(self):
        with patch('tools.component_inventory.importlib.metadata.distribution', return_value=self.dist):
            return inventory('fixture')

    def test_modified_installed_code_rejected(self):
        path = self.add('fixture/native.so', b'approved-native')
        path.write_bytes(b'changed-native!')
        with self.assertRaisesRegex(ValueError, 'RECORD'):
            self.collect()

    def test_missing_installed_code_rejected(self):
        self.add('fixture/module.py', b'pass').unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.collect()

    def test_nested_metadata_vendor_pins_and_sboms_preserved(self):
        self.add('fixture/_vendor/dependency.dist-info/METADATA',
                 b'Name: dependency\nVersion: 2.1\nLicense-Expression: MIT\n')
        self.add('fixture/_vendor/vendor.txt', b'# declared bundles\nother==3.2\n')
        self.add('fixture.dist-info/sboms/bom.json', b'{"components":[{"name":"native"}]}')
        result = self.collect()
        self.assertEqual(result['verified_record_files'], 3)
        self.assertEqual(result['bundled_metadata'][0]['version'], '2.1')
        self.assertEqual(result['vendor_pins'][0]['name'], 'other')
        self.assertEqual(result['sboms'][0]['document']['components'][0]['name'], 'native')

    def test_unknown_vendor_declaration_rejected(self):
        self.add('fixture/_vendor/vendor.txt', b'unpinned>=2\n')
        with self.assertRaisesRegex(ValueError, 'vendor declaration'):
            self.collect()

    def test_unhashed_code_is_not_treated_as_installer_metadata(self):
        self.add('fixture/native.so', b'native')
        self.files[0].hash = None
        with self.assertRaisesRegex(ValueError, 'unhashed'):
            self.collect()
