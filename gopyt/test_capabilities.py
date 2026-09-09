"""Authority must cover every operation, including cached and batched access."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gopyt.storage import Store, StorageError


class Capabilities(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(os.path.realpath(self.temp.name))
        self.root = base / 'app'; self.root.mkdir()
        self.policy = base / 'policy.json'
        self.db = Store(self.root)
        self.db.put('tenant/a/item', 'a')
        self.db.put('tenant/ab/item', 'ab')
        self.db.put('tenant/b/item', 'b')
        self.env = patch.dict(os.environ, {'GOPYT_DB_POLICY_FILE': str(self.policy)})
        self.env.start(); self.addCleanup(self.env.stop)
        self.configure(['tenant/a/'], ['tenant/a/'])

    def configure(self, read, write):
        self.policy.write_text(json.dumps({'version': 1, 'read': read, 'write': write}))
        self.policy.chmod(0o600)

    def test_namespace_does_not_grant_nearby_tenant_or_unscoped_keys(self):
        self.assertEqual(self.db.get('tenant/a/item'), 'a')
        for key in ('tenant/ab/item', 'tenant/b/item', 'tenant/a', 'item'):
            for call in (lambda: self.db.get(key), lambda: self.db.put(key, 'bad'),
                         lambda: self.db.compare_exchange(key, None, 'bad')):
                with self.assertRaises(StorageError): call()

    def test_batch_denial_is_all_or_nothing(self):
        with self.assertRaises(StorageError):
            self.db.compare_exchange_many([('tenant/a/item', 'a', 'changed'),
                                           ('tenant/b/item', 'b', 'changed')])
        self.assertEqual(self.db.get('tenant/a/item'), 'a')
        with self.assertRaises(StorageError):
            self.db.get_many(['tenant/a/item', 'tenant/b/item'])

    def test_revocation_precedes_cache_and_read_write_are_separate(self):
        self.assertEqual(self.db.get('tenant/a/item'), 'a')
        self.configure([], ['tenant/a/'])
        with self.assertRaises(StorageError): self.db.get('tenant/a/item')
        self.db.put('tenant/a/item', 'new')
        with self.assertRaises(StorageError): self.db.compare_exchange('tenant/a/item', 'new', 'bad')
        self.configure(['tenant/a/'], [])
        self.assertEqual(self.db.get('tenant/a/item'), 'new')
        with self.assertRaises(StorageError): self.db.put('tenant/a/item', 'bad')

    def test_explicit_wildcard_and_empty_deny_all(self):
        self.configure(['*'], [])
        self.assertEqual(self.db.get('tenant/b/item'), 'b')
        self.configure([], [])
        with self.assertRaises(StorageError): self.db.get_many(['tenant/a/item'])

    def test_malformed_missing_or_public_policy_fails_closed(self):
        for data in ('{}', 'null', '{"version":1,"version":1,"read":[],"write":[]}',
                     '{"version":true,"read":[],"write":[]}',
                     '{"version":1,"read":["tenant/a"],"write":[]}',
                     '{"version":1,"read":["tenant/*/"],"write":[]}',
                     '{"version":1,"read":[],"write":[],"typo":true}'):
            self.policy.write_text(data)
            with self.subTest(data=data), self.assertRaises(StorageError):
                self.db.get('tenant/a/item')
        self.configure(['*'], ['*']); self.policy.chmod(0o644)
        with self.assertRaises(StorageError): self.db.get('tenant/a/item')
        self.policy.unlink()
        with self.assertRaises(StorageError): self.db.get('tenant/a/item')


if __name__ == '__main__':
    unittest.main()
