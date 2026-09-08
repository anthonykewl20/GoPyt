import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from gopyt.files import regular_file
from gopyt.natives import _safe_path


class FilesystemSecurity(unittest.TestCase):
    def test_hardlinks_cannot_read_or_modify_outside_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'package';root.mkdir();outside=Path(temp)/'outside';outside.write_bytes(b'private')
            os.link(outside,root/'linked.bin')
            for write in (False,True):
                with self.assertRaises(OSError):
                    with regular_file(str(root),'linked.bin',write=write):pass
                self.assertEqual(outside.read_bytes(),b'private')
    def test_runtime_metadata_and_source_writes_are_reserved(self):
        with tempfile.TemporaryDirectory() as root:
            vm=SimpleNamespace(root=root)
            for path in ['.gopyt-state/store.sqlite3','.git/config','data/.git/config']:
                self.assertIsNone(_safe_path(vm,path))
            for path in ['spec/rule.gopyt','impl/rule.gopyt','gopyt.lock','gopyt.toml','build/out.gobyte','helper.py']:
                self.assertIsNone(_safe_path(vm,path,write=True))
            self.assertIsNotNone(_safe_path(vm,'records.bin',write=True))
