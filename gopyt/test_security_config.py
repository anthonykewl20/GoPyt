"""Host security controls exercised through persistent storage boundaries."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from gopyt.security_config import SecurityError, http_token, secret_file, MAGIC
from gopyt.storage import Store, StorageError, DIRECTORY, DATABASE


class SecurityProfile(unittest.TestCase):
    def test_storage_key_descriptor_admission_and_private_file_cleanup(self):
        from gopyt.resource_budget import ResourceBudget, ResourceLimits
        from gopyt.resource_descriptors import DescriptorRegistry
        class Context:
            deadline_ns = None
            def __init__(self, registry): self.descriptors = registry
            def check_cancelled(self): pass
        for capacity in (0, 1):
            with self.subTest(capacity=capacity):
                budget = ResourceBudget(ResourceLimits(0, 0, capacity, 0))
                registry = DescriptorRegistry(budget)
                context = Context(registry)
                store = Store(str(self.root), context=context)
                with self.assertRaisesRegex(StorageError, 'resource budget'):
                    store.put('key', 'value')
                self.assertFalse((self.root / DIRECTORY).exists())
                self.assertEqual(registry.pending(), 0)
                self.assertEqual(budget.snapshot()['active_reservations'], 0)
                self.assertTrue(registry.close())
        budget = ResourceBudget(ResourceLimits(0, 0, 2, 0))
        registry = DescriptorRegistry(budget)
        self.addCleanup(registry.close)
        self.assertEqual(secret_file(str(self.key), self.root, 32, descriptors=registry),
                         self.key.read_bytes())
        self.assertEqual(budget.snapshot()['peak']['descriptors'], 2)
        self.key.chmod(0o644)
        with self.assertRaises(SecurityError):
            secret_file(str(self.key), self.root, 32, descriptors=registry)
        self.assertEqual(registry.pending(), 0)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'app'
        self.root.mkdir()
        self.key = self.base / 'key'
        self.key.write_bytes(os.urandom(32))
        self.key.chmod(0o600)
        self.token = self.base / 'token'
        self.token.write_bytes(b'x' * 48)
        self.token.chmod(0o600)
        env = {'GOPYT_SECURITY_PROFILE': 'strict',
               'GOPYT_STORE_KEY_FILE': str(self.key), 'GOPYT_STORE_ID': 'test-store',
               'GOPYT_HTTP_TOKEN_FILE': str(self.token)}
        context = patch.dict(os.environ, env)
        context.start()
        self.addCleanup(context.stop)

    def test_encrypted_restart_and_conditional_write(self):
        store = Store(str(self.root))
        store.put('secret', 'sensitive-value-unique')
        raw = (self.root / DIRECTORY / DATABASE).read_bytes()
        self.assertTrue(raw.startswith(MAGIC))
        self.assertNotIn(b'sensitive-value-unique', raw)
        self.assertEqual(Store(str(self.root)).get('secret'), 'sensitive-value-unique')
        self.assertTrue(store.compare_exchange('secret', 'sensitive-value-unique', 'next'))
        self.assertEqual(Store(str(self.root)).get('secret'), 'next')

    def test_wrong_key_context_and_missing_key_reject_even_cached_reads(self):
        store = Store(str(self.root))
        store.put('key', 'value')
        self.assertEqual(store.get('key'), 'value')
        original = self.key.read_bytes()
        self.key.write_bytes(os.urandom(32))
        with self.assertRaises(StorageError): store.get('key')
        self.key.write_bytes(original)
        with patch.dict(os.environ, {'GOPYT_STORE_ID': 'other'}):
            with self.assertRaises(StorageError): store.get('key')
        with patch.dict(os.environ, {'GOPYT_STORE_KEY_FILE': ''}):
            with self.assertRaises(StorageError): store.get('key')
        self.assertEqual(store.get('key'), 'value')

    def test_tampered_snapshot_cannot_be_read_or_overwritten(self):
        Store(str(self.root)).put('key', 'value')
        path = self.root / DIRECTORY / DATABASE
        raw = bytearray(path.read_bytes())
        raw[-1] ^= 1
        path.write_bytes(raw)
        with self.assertRaises(StorageError): Store(str(self.root)).get('key')
        with self.assertRaises(StorageError): Store(str(self.root)).put('key', 'new')
        self.assertEqual(path.read_bytes(), raw)

    def test_plaintext_is_not_silently_migrated(self):
        with patch.dict(os.environ, {'GOPYT_SECURITY_PROFILE': 'development', 'GOPYT_STORE_KEY_FILE': ''}):
            Store(str(self.root)).put('key', 'plain')
        with self.assertRaises(StorageError): Store(str(self.root)).get('key')

    def test_secret_permissions_links_and_package_location(self):
        self.key.chmod(0o644)
        with self.assertRaises(SecurityError): secret_file(str(self.key), self.root, 32)
        self.key.chmod(0o600)
        linked = self.base / 'linked'
        os.link(self.key, linked)
        with self.assertRaises(SecurityError): secret_file(str(self.key), self.root, 32)
        linked.unlink()
        linked.symlink_to(self.key)
        with self.assertRaises(SecurityError): secret_file(str(linked), self.root, 32)
        inside = self.root / 'key'
        inside.write_bytes(b'z' * 32)
        inside.chmod(0o600)
        with self.assertRaises(SecurityError): secret_file(str(inside), self.root, 32)

    def test_strict_listener_requires_loopback_and_token(self):
        self.assertEqual(http_token(self.root, ('127.0.0.1', 8080)), b'Bearer ' + b'x' * 48)
        with self.assertRaises(SecurityError): http_token(self.root, ('0.0.0.0', 8080))
        with patch.dict(os.environ, {'GOPYT_HTTP_TOKEN_FILE': ''}):
            with self.assertRaises(SecurityError): http_token(self.root, ('127.0.0.1', 8080))
        with patch.dict(os.environ, {'GOPYT_SECURITY_PROFILE': 'typo'}):
            with self.assertRaises(SecurityError): http_token(self.root, ('127.0.0.1', 8080))

    def test_real_http_authentication_encrypted_restart(self):
        import http.client
        from tools.inventory_probe import Server, command
        with Server() as server:
            server.headers = {'Authorization': 'Bearer ' + 'x' * 48}
            server.start()
            for headers in ({}, {'Authorization': 'Bearer wrong'}):
                conn = http.client.HTTPConnection('127.0.0.1', server.port, timeout=5)
                try:
                    conn.request('POST', '/inventory', body=b'not-json', headers=headers)
                    response = conn.getresponse()
                    self.assertEqual(response.status, 401)
                    response.read()
                finally: conn.close()
            conn = http.client.HTTPConnection('127.0.0.1', server.port, timeout=5)
            try:
                conn.putrequest('GET', '/inventory')
                for _ in range(2): conn.putheader('Authorization', 'Bearer ' + 'x' * 48)
                conn.endheaders()
                response = conn.getresponse()
                self.assertEqual(response.status, 401)
                response.read()
            finally: conn.close()
            self.assertEqual(server.request('GET')['state']['entries'], [])
            first = server.request(body=command(ident='authorized'))
            self.assertEqual(first['outcome'], 'active')
            raw = (server.root / DIRECTORY / DATABASE).read_bytes()
            self.assertTrue(raw.startswith(MAGIC))
            self.assertNotIn(b'authorized', raw)
            server.stop(abrupt=True)
            server.start()
            self.assertEqual(server.request(body=command(ident='authorized')), first)
