"""Pinned CI interpreter installation rejects mismatches before activation."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools.install_ci_python import install


class InterpreterInstall(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.archive = self.root / 'input.tar.gz'
        self.destination = self.root / 'installed'

    def pin(self, content):
        self.archive.write_bytes(content)
        return {'url': self.archive.as_uri(), 'sha256': hashlib.sha256(content).hexdigest(),
                'size': len(content), 'version': '3.11.16'}

    def bundle(self, name):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w:gz') as archive:
            item = tarfile.TarInfo(name)
            item.size = 1
            archive.addfile(item, io.BytesIO(b'x'))
        return data.getvalue()

    def test_wrong_hash_never_opens_archive(self):
        pin = self.pin(b'not a tar file')
        pin['sha256'] = '0' * 64
        with patch('tools.install_ci_python.tarfile.open', side_effect=AssertionError('must not parse')):
            with self.assertRaisesRegex(ValueError, 'identity'):
                install(pin, self.destination)
        self.assertFalse((self.destination / 'python').exists())

    def test_size_mismatch_rejects_download(self):
        pin = self.pin(b'bytes')
        pin['size'] = 1
        with self.assertRaisesRegex(ValueError, 'size'):
            install(pin, self.destination)

    def test_filtered_extraction_rejects_escape(self):
        pin = self.pin(self.bundle('../escaped'))
        with self.assertRaises(tarfile.FilterError):
            install(pin, self.destination)
        self.assertFalse((self.root / 'escaped').exists())

    def test_version_mismatch_does_not_provide_python_command(self):
        pin = self.pin(self.bundle('python/bin/python3'))
        result = subprocess.CompletedProcess([], 0, json.dumps([3, 10, 0]), '')
        with patch('tools.install_ci_python.subprocess.run', return_value=result):
            with self.assertRaisesRegex(ValueError, 'version mismatch'):
                install(pin, self.destination)
        self.assertFalse((self.destination / 'python/bin/python').exists())
