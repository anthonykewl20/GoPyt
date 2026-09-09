"""Rotation preserves authenticated data and old-or-new publication semantics."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from gopyt.security_config import MAGIC
from gopyt.storage import Store, StorageError, DIRECTORY, DATABASE


class KeyRotation(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve();self.root=self.base/'app';self.root.mkdir()
        self.old=os.urandom(32);self.new=os.urandom(32)
        self.key=self.base/'key';self.key.write_bytes(self.old);self.key.chmod(0o600)
        self.ring=self.base/'ring.json'
        self.env=patch.dict(os.environ,{'GOPYT_SECURITY_PROFILE':'strict',
            'GOPYT_STORE_KEY_FILE':str(self.key),'GOPYT_STORE_KEYRING_FILE':'',
            'GOPYT_STORE_ID':'rotation-test','GOPYT_DB_POLICY_FILE':''})
        self.env.start();self.addCleanup(self.env.stop)
        # Unconfigured authority preserves the existing package-wide policy.
        os.environ.pop('GOPYT_DB_POLICY_FILE',None)
        self.db=Store(str(self.root))
        self.db.compare_exchange_many([('a',None,'sensitive-a'),('b',None,'sensitive-b')])
        self.path=self.root/DIRECTORY/DATABASE

    def configure(self,active='new',include_old=True):
        keys={'new':self.new.hex()}
        if include_old:keys['old']=self.old.hex()
        self.ring.write_text(json.dumps({'version':1,'active':active,'keys':keys}))
        self.ring.chmod(0o600)
        os.environ['GOPYT_STORE_KEY_FILE']=''
        os.environ['GOPYT_STORE_KEYRING_FILE']=str(self.ring)

    def test_rekey_legacy_snapshot_then_retire_old_key(self):
        legacy=self.path.read_bytes();self.configure()
        self.assertEqual(self.db.get_many(['a','b']),['sensitive-a','sensitive-b'])
        result=subprocess.run([sys.executable,'-m','gopyt.store_admin','rekey','--root',str(self.root)],
                              capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'committed')
        self.assertNotEqual(self.path.read_bytes(),legacy)
        self.assertTrue(self.path.read_bytes().startswith(MAGIC))
        self.assertNotIn(b'sensitive-a',self.path.read_bytes())
        self.configure(include_old=False)
        self.assertEqual(Store(self.root).get_many(['a','b']),['sensitive-a','sensitive-b'])
        with patch.dict(os.environ,{'GOPYT_STORE_KEY_FILE':str(self.key),'GOPYT_STORE_KEYRING_FILE':''}):
            with self.assertRaises(StorageError):Store(self.root).get('a')

    def test_retiring_key_early_invalidates_cached_plaintext(self):
        self.assertEqual(self.db.get('a'),'sensitive-a')
        self.configure(include_old=False)
        with self.assertRaises(StorageError):self.db.get('a')
        with self.assertRaises(StorageError):self.db.get_many(['a','b'])
        self.configure();self.assertEqual(self.db.get('a'),'sensitive-a')

    def test_failed_publish_keeps_old_key_readable_and_retry_succeeds(self):
        original=self.path.read_bytes();self.configure()
        with patch('gopyt.storage.os.replace',side_effect=OSError('injected')):
            with self.assertRaises(StorageError):self.db.rekey()
        self.assertEqual(self.path.read_bytes(),original)
        self.assertEqual(Store(self.root).get('a'),'sensitive-a')
        self.db.rekey();self.configure(include_old=False)
        self.assertEqual(Store(self.root).get('b'),'sensitive-b')

    def test_corruption_is_not_reencrypted_or_overwritten(self):
        raw=bytearray(self.path.read_bytes());raw[-1]^=1;self.path.write_bytes(raw)
        self.configure()
        with self.assertRaises(StorageError):self.db.rekey()
        self.assertEqual(self.path.read_bytes(),raw)

    def test_multiple_key_sources_and_malformed_rings_rejected(self):
        self.configure()
        with patch.dict(os.environ,{'GOPYT_STORE_KEY_FILE':str(self.key)}):
            with self.assertRaises(StorageError):self.db.get('a')
        for data in ('{}','null','{"version":1,"active":"missing","keys":{}}',
                     json.dumps({'version':True,'active':'new','keys':{'new':self.new.hex()}}),
                     json.dumps({'version':1,'active':'new','keys':{'new':'z'*64}}),
                     json.dumps({'version':1,'active':'new','keys':{'new':self.new.hex(),'duplicate':self.new.hex()}})):
            self.ring.write_text(data)
            with self.assertRaises(StorageError):self.db.get('a')

    def test_encrypted_batch_conflict_and_delete_survive_restart(self):
        self.configure()
        self.assertFalse(self.db.compare_exchange_many([('a','stale','bad'),('b','sensitive-b',None)]))
        self.assertTrue(self.db.compare_exchange_many([('a','sensitive-a','new'),('b','sensitive-b',None)]))
        self.configure(include_old=False)
        self.assertEqual(Store(self.root).get_many(['a','b']),['new',None])


if __name__=='__main__':unittest.main()
