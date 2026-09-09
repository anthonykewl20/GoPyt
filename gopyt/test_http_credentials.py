"""Live service-token admission, revocation and reload failure recovery."""
import http.client
import os
from pathlib import Path
import secrets
import tempfile
import threading
import unittest
from unittest.mock import patch

from gopyt.test_app_runtime import running_server
from gopyt.values import UNIT


class HttpCredentials(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.path = self.base / 'token'
        self.old, self.new = secrets.token_hex(24), secrets.token_hex(24)
        self.replace(self.old)
        env = patch.dict(os.environ, {'GOPYT_SECURITY_PROFILE':'development',
            'GOPYT_HTTP_TOKEN_FILE':str(self.path), 'GOPYT_STORE_ANCHOR_DIR':''})
        env.start(); self.addCleanup(env.stop)

    def replace(self, value):
        pending = self.base / 'next'
        pending.write_text(value); pending.chmod(0o600); pending.replace(self.path)

    @staticmethod
    def request(port, token, connection=None):
        owned = connection is None
        connection = connection or http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        try:
            connection.request('GET', '/echo/abc', headers={'Authorization':'Bearer '+token})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            if owned: connection.close()

    def test_rotates_on_live_keepalive_connection(self):
        with running_server(MAX_HANDLERS=2, QUEUE=4) as (_, port):
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            try:
                self.assertEqual(self.request(port, self.old, connection)[0], 200)
                original_socket = connection.sock
                self.replace(self.new)
                self.assertEqual(self.request(port, self.new, connection)[0], 200)
                self.assertIs(connection.sock, original_socket)
                self.assertEqual(self.request(port, self.old), (401, b''))
            finally: connection.close()

    def test_reload_failures_never_reach_handler_and_recover(self):
        calls = []
        with running_server(MAX_HANDLERS=2, QUEUE=4) as (vm, port):
            vm.natives = dict(vm.natives)
            vm.natives['core.log.write'] = lambda *args: calls.append(1) or UNIT
            for failure in ('missing', 'short', 'permissions', 'symlink'):
                with self.subTest(failure=failure):
                    self.replace(self.old)
                    if failure == 'missing': self.path.unlink()
                    elif failure == 'short': self.replace('short')
                    elif failure == 'permissions': self.path.chmod(0o644)
                    else:
                        saved = self.base / 'saved'; self.path.rename(saved); self.path.symlink_to(saved)
                    self.assertEqual(self.request(port, self.old), (503, b''))
                    self.assertEqual(calls, [])
            self.replace(self.new)
            self.assertEqual(self.request(port, self.new)[0], 200)
            self.assertEqual(calls, [1])

    def test_startup_path_and_mode_cannot_be_disabled_by_environment_change(self):
        other = self.base / 'other'; other.write_text(self.new); other.chmod(0o600)
        with running_server(MAX_HANDLERS=2, QUEUE=4) as (_, port):
            for value in ('', str(other)):
                with patch.dict(os.environ, {'GOPYT_HTTP_TOKEN_FILE':value}):
                    self.assertEqual(self.request(port, self.old)[0], 200)
                    self.assertEqual(self.request(port, self.new)[0], 401)
            self.replace(self.new)
            self.assertEqual(self.request(port, self.new)[0], 200)

    def test_previously_admitted_request_finishes_after_rotation(self):
        entered, release = threading.Event(), threading.Event()
        outcomes = []
        with running_server(MAX_HANDLERS=2, QUEUE=4) as (vm, port):
            vm.natives = dict(vm.natives)
            def hold(*args):
                entered.set()
                if not release.wait(5): raise AssertionError('release watchdog')
                return UNIT
            vm.natives['core.log.write'] = hold
            caller = threading.Thread(target=lambda: outcomes.append(self.request(port, self.old)))
            caller.start()
            try:
                self.assertTrue(entered.wait(5))
                self.replace(self.new)
                self.assertEqual(self.request(port, self.old), (401, b''))
                release.set(); caller.join(5)
                self.assertFalse(caller.is_alive())
                self.assertEqual(outcomes[0][0], 200)
                self.assertEqual(self.request(port, self.new)[0], 200)
            finally:
                release.set(); caller.join(5)


if __name__ == '__main__':
    unittest.main()
