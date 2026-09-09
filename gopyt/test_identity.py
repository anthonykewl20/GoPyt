"""Verified bearer identity, tenant native authority and real HTTP policies."""
import concurrent.futures
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gopyt.cli import build
from gopyt.identity import SessionBroker
from gopyt.resource_authority import ResourceAuthority, AuthorityError
from gopyt.values import UNIT
from gopyt.vm import VM


class Sessions(unittest.TestCase):
    def setUp(self):
        self.root = ResourceAuthority.issue(database_read=['tenant/'], database_write=['tenant/'],
                                            file_read=['tenants/'])
        self.broker = SessionBroker(self.root, module='probe')
        self.broker.provision('alice', 'alpha', routes=[('POST', '/read')],
                              database_read=['tenant/alpha/'])

    def test_verified_identity_and_immutable_policy(self):
        token = self.broker.issue('alice', 'alpha')
        session = self.broker.authenticate('Bearer ' + token)
        self.assertEqual((session.identity.subject, session.identity.tenant), ('alice', 'alpha'))
        self.assertNotIn(token, repr(self.broker._sessions))
        self.assertTrue(session.authority.admits([('database_read', 'tenant/alpha/key')]))
        self.assertFalse(session.authority.admits([('database_read', 'tenant/beta/key')]))
        self.assertFalse(session.authority.admits([('database_write', 'tenant/alpha/key')]))
        self.assertIsNone(self.broker.authenticate('Bearer ' + token[:-1] + ('a' if token[-1] != 'a' else 'b')))
        self.assertIsNone(self.broker.authenticate('Bearer ' + token + ', Bearer extra'))
        self.assertIsNone(self.broker.authenticate('Bearer ' + '\N{SNOWMAN}' * 43))
        self.assertIs(self.broker.authenticate('bearer ' + token), session)
        with self.assertRaises(AuthorityError): self.broker.issue('mallory', 'alpha')
        with self.assertRaises(AuthorityError): self.broker.issue('alice', 'beta')
        with self.assertRaises(AuthorityError): self.broker.issue('alice', 'alpha', ttl_ms=True)

    def test_policy_replacement_revoke_and_reprovision_invalidate_old_credentials(self):
        token = self.broker.issue('alice', 'alpha')
        session = self.broker.authenticate('Bearer ' + token)
        self.broker.provision('alice', 'alpha', routes=[])
        self.assertIsNone(self.broker.authenticate('Bearer ' + token))
        self.assertFalse(session.authority.admits([('database_read', 'tenant/alpha/key')]))
        fresh = self.broker.issue('alice', 'alpha')
        self.broker.revoke('alice', 'alpha')
        self.broker.provision('alice', 'alpha', routes=[('POST', '/read')], database_read=['tenant/alpha/'])
        self.assertIsNone(self.broker.authenticate('Bearer ' + fresh))
        newer = self.broker.issue('alice', 'alpha')
        self.broker.revoke_session(newer)
        self.assertIsNone(self.broker.authenticate('Bearer ' + newer))

    def test_expiry_applies_to_retained_authority_and_descendants_without_wall_clock(self):
        with patch('gopyt.resource_authority.time.monotonic_ns', return_value=10_000_000):
            token = self.broker.issue('alice', 'alpha', ttl_ms=10)
            session = self.broker.authenticate('Bearer ' + token)
            child = session.authority.delegate(database_read=['tenant/alpha/'])
        with patch('gopyt.resource_authority.time.monotonic_ns', return_value=20_000_000), \
                patch('time.time', return_value=-10000):
            self.assertFalse(child.admits([('database_read', 'tenant/alpha/key')]))
            self.assertIsNone(self.broker.authenticate('Bearer ' + token))

    def test_namespace_escape_invalid_routes_and_capacity_fail_closed(self):
        for rights in ({'database_read': ['*']}, {'database_read': ['tenant/beta/']},
                       {'file_read': ['tenants/alpha/../beta/']}, {'listen': ['*']},
                       {'ttl_ms': 10}):
            with self.subTest(rights=rights), self.assertRaises(AuthorityError):
                self.broker.provision('alice', 'alpha', **rights)
        with self.assertRaises(AuthorityError):
            self.broker.provision('alice', '../beta')
        with self.assertRaises(AuthorityError):
            self.broker.provision('alice', 'alpha', routes=[('BOGUS', '/read')])
        with patch('gopyt.identity.MAX_SESSIONS', 1):
            token = self.broker.issue('alice', 'alpha')
            with self.assertRaises(AuthorityError): self.broker.issue('alice', 'alpha')
            self.broker.revoke_session(token)
            self.assertIsNotNone(self.broker.issue('alice', 'alpha'))

    def test_mutated_configuration_does_not_change_issued_authority(self):
        grants = ['tenant/alpha/']; routes = [['POST', '/read']]
        self.broker.provision('alice', 'alpha', routes=routes, database_read=grants)
        grants[0] = '*'; routes[0][1] = '/commit'
        token = self.broker.issue('alice', 'alpha')
        session = self.broker.authenticate('Bearer ' + token)
        self.assertEqual(session.routes, frozenset([('POST', '/read')]))
        self.assertFalse(session.authority.admits([('database_read', 'tenant/beta/key')]))

    def test_identity_and_expiring_authority_follow_parallel_children(self):
        from gopyt.test_resource_authority import ResourceCalls
        fixture = ResourceCalls(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        broker = SessionBroker(fixture.authority, module='probe')
        broker.provision('alice', 'alpha', database_read=['tenant/alpha/'])
        session = broker.authenticate('Bearer ' + broker.issue('alice', 'alpha'))
        observed = []
        original = fixture.vm.call
        def call(fn_id, *args, **kwargs):
            if fixture.vm.names[fn_id].startswith('arm:'):
                observed.append(fixture.vm.request_identity)
            return original(fn_id, *args, **kwargs)
        with patch.object(fixture.vm, 'call', side_effect=call), fixture.vm.request_scope(session):
            result = fixture.call('parallel_read')
            fixture.status(result[0], 'DbError')
            fixture.status(result[1], 'DbError')
        self.assertEqual(observed, [session.identity, session.identity])
        self.assertIsNone(fixture.vm.request_identity)

    def test_strict_sessions_require_loopback_keys_and_unambiguous_auth_mode(self):
        from gopyt.security_config import http_token, SecurityError
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve(); root = base / 'app'; root.mkdir()
            key = base / 'key'; key.write_bytes(os.urandom(32)); key.chmod(0o600)
            env = {k:v for k,v in os.environ.items() if not k.startswith('GOPYT_')}
            env.update(GOPYT_SECURITY_PROFILE='strict', GOPYT_STORE_KEY_FILE=str(key),
                       GOPYT_STORE_ID='identity-test')
            with patch.dict(os.environ, env, clear=True):
                self.assertIsNone(http_token(root, ('127.0.0.1', 8080), session_auth=True))
                with self.assertRaises(SecurityError):
                    http_token(root, ('0.0.0.0', 8080), session_auth=True)
                with patch.dict(os.environ, {'GOPYT_STORE_KEY_FILE':''}), self.assertRaises(SecurityError):
                    http_token(root, ('127.0.0.1', 8080), session_auth=True)
                with patch.dict(os.environ, {'GOPYT_HTTP_TOKEN_FILE':str(key)}), self.assertRaises(SecurityError):
                    http_token(root, ('127.0.0.1', 8080), session_auth=True)


class HttpIdentities(unittest.TestCase):
    strict_profile = False

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve(); self.root = self.base / 'retail'
        source = Path(__file__).resolve().parents[1] / 'examples/retail_replay'
        shutil.copytree(source, self.root, ignore=shutil.ignore_patterns('build', '.gopyt-state', '.gopyt-transaction.lock'))
        # Explicitly clear unrelated host deployment configuration from fixtures.
        clean = {k: v for k, v in os.environ.items() if not k.startswith('GOPYT_')}
        context = patch.dict(os.environ, clean, clear=True); context.start(); self.addCleanup(context.stop)
        if self.strict_profile:
            key = self.base / 'storage.key'
            key.write_bytes(os.urandom(32)); key.chmod(0o600)
            os.environ.update(GOPYT_SECURITY_PROFILE='strict', GOPYT_STORE_KEY_FILE=str(key),
                              GOPYT_STORE_ID='identity-http-tests')
        _, art, self.ids = build(str(self.root))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); self.port = sock.getsockname()[1]
        self.authority = ResourceAuthority.issue(database_read=['tenant/'], database_write=['tenant/'],
                                                  listen=[f'127.0.0.1:{self.port}'])
        self.broker = SessionBroker(self.authority, module='retail')
        self.provision('alice', 'alpha'); self.provision('bob', 'beta')
        self.alice = self.broker.issue('alice', 'alpha'); self.bob = self.broker.issue('bob', 'beta')
        self.vm = VM(art, str(self.root), authority=self.authority, identities=self.broker)
        self.vm.db.put('tenant/alpha/key', 'a'); self.vm.db.put('tenant/beta/key', 'b')
        os.environ['GOPYT_HTTP_ADDR'] = f'127.0.0.1:{self.port}'
        limit = patch('gopyt.server.MAX_HANDLERS', 4); limit.start(); self.addCleanup(limit.stop)
        self.results = []
        def serve():
            try: self.results.append(self.vm.call(self.ids['retail.serve'], []))
            except BaseException as exc: self.results.append(exc)
        self.thread = threading.Thread(target=serve); self.thread.start(); self.addCleanup(self.stop)
        deadline = time.monotonic() + 3
        while getattr(self.vm, 'httpd', None) is None and not self.results and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(getattr(self.vm, 'httpd', None), self.results)

    def provision(self, subject, tenant, write=True):
        self.broker.provision(subject, tenant,
            routes=[('POST', '/read')] + ([('POST', '/commit')] if write else []),
            database_read=[f'tenant/{tenant}/'], database_write=[f'tenant/{tenant}/'] if write else [])

    def stop(self):
        if getattr(self.vm, 'httpd', None) is not None:
            self.vm.httpd.shutdown()
        self.thread.join(3)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.results, [UNIT])

    def request(self, path, body, token=None, extra=()):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        payload = json.dumps(body).encode()
        try:
            connection.putrequest('POST', path)
            connection.putheader('Content-Type', 'application/json')
            connection.putheader('Content-Length', str(len(payload)))
            if token is not None: connection.putheader('Authorization', 'Bearer ' + token)
            for name, value in extra: connection.putheader(name, value)
            connection.endheaders(payload)
            response = connection.getresponse(); data = response.read()
            return response.status, json.loads(data) if data else None
        finally:
            connection.close()

    def test_cross_tenant_reads_writes_batch_and_forged_identity_headers(self):
        self.assertEqual(self.request('/read', {'keys':['tenant/alpha/key']}, self.alice),
                         (200, {'outcome':'ok', 'values':['a']}))
        self.assertEqual(self.request('/read', {'keys':['tenant/beta/key']}, self.bob)[1]['values'], ['b'])
        self.assertEqual(self.request('/read', {'keys':['tenant/beta/key']}, self.alice,
            [('X-Tenant-Id','beta'), ('X-User','bob')]), (200, {'outcome':'db_error','values':[]}))
        changes = [{'key':'tenant/alpha/key','expected':'a','value':'changed'},
                   {'key':'tenant/beta/key','expected':'b','value':'stolen'}]
        self.assertEqual(self.request('/commit', {'changes':changes}, self.alice), (200, {'outcome':'db_error'}))
        self.assertEqual(self.vm.db.get_many(['tenant/alpha/key','tenant/beta/key']), ['a','b'])
        self.assertEqual(self.request('/commit', {'changes':changes[:1]}, self.alice), (200, {'outcome':'committed'}))
        self.assertEqual(self.vm.db.get('tenant/alpha/key'), 'changed')

    def test_wrong_audience_and_reused_broker_are_refused(self):
        other = SessionBroker(self.authority, module='other')
        wrong = VM(self.vm.art, str(self.root), authority=self.authority, identities=other)
        result = wrong.call(self.ids['retail.serve'], [])
        self.assertEqual(wrong.type_name(result.type_id), 'core.status.ListenError')
        self.assertFalse(wrong.serving)
        with self.assertRaises(AuthorityError):
            VM(self.vm.art, str(self.root), authority=self.authority, identities=self.broker)

    def test_missing_duplicate_revoked_expired_and_restart_credentials(self):
        payload = {'keys':['tenant/alpha/key']}
        self.assertEqual(self.request('/read', payload)[0], 401)
        self.assertEqual(self.request('/read', payload, self.alice,
                         [('Authorization', 'Bearer '+self.bob)])[0], 401)
        token = self.broker.issue('alice', 'alpha', ttl_ms=1)
        time.sleep(0.005)
        self.assertEqual(self.request('/read', payload, token)[0], 401)
        self.broker.revoke_session(self.alice)
        self.assertEqual(self.request('/read', payload, self.alice)[0], 401)
        restarted = SessionBroker(self.authority, module='retail')
        self.assertIsNone(restarted.authenticate('Bearer ' + self.bob))

    def test_policy_change_denies_stale_credentials_and_route_escalation(self):
        self.provision('alice', 'alpha', write=False)
        self.assertEqual(self.request('/read', {'keys':['tenant/alpha/key']}, self.alice)[0], 401)
        reader = self.broker.issue('alice', 'alpha')
        changes = [{'key':'tenant/alpha/key','expected':'a','value':'bad'}]
        self.assertEqual(self.request('/commit', {'changes':changes}, reader)[0], 403)
        self.assertEqual(self.vm.db.get('tenant/alpha/key'), 'a')

    def test_reused_http_connection_reauthenticates_every_request(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        self.addCleanup(connection.close)
        saved_socket = None
        for token, tenant, expected in ((self.alice, 'alpha', {'outcome':'ok','values':['a']}),
                                       (self.bob, 'beta', {'outcome':'ok','values':['b']}),
                                       (self.alice, 'beta', {'outcome':'db_error','values':[]})):
            connection.request('POST', '/read', json.dumps({'keys':[f'tenant/{tenant}/key']}),
                               headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read()), expected)
            if saved_socket is None: saved_socket = connection.sock
            else: self.assertIs(connection.sock, saved_socket)

    def test_concurrent_users_and_policy_changes_do_not_cross_scopes(self):
        original = self.vm.db.get_many
        identities = []; lock = threading.Lock()
        def read(keys):
            context = self.vm.request_identity
            with lock: identities.append((context.subject, context.tenant, tuple(keys)))
            return original(keys)
        with patch.object(self.vm.db, 'get_many', side_effect=read):
            def request(index):
                tenant, token = ('alpha', self.alice) if index % 2 == 0 else ('beta', self.bob)
                return tenant, self.request('/read', {'keys':[f'tenant/{tenant}/key']}, token)
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                jobs = [pool.submit(request, i) for i in range(24)]
                self.provision('alice', 'alpha', write=False)
                for job in jobs:
                    tenant, result = job.result()
                    if result[0] == 200 and result[1]['outcome'] == 'ok':
                        self.assertEqual(result[1]['values'], ['a' if tenant == 'alpha' else 'b'])
                    else:
                        self.assertEqual(tenant, 'alpha')
                        self.assertIn(result, [(401, None), (200, {'outcome':'db_error','values':[]})])
        self.assertTrue(identities)
        for subject, tenant, keys in identities:
            self.assertEqual(subject, 'alice' if tenant == 'alpha' else 'bob')
            self.assertTrue(all(k.startswith(f'tenant/{tenant}/') for k in keys))
        self.assertIsNone(self.vm.request_identity)

    def test_expiry_and_policy_revocation_between_authentication_and_resource_operation(self):
        for mode in ('expiry', 'revocation'):
            with self.subTest(mode=mode):
                clock = [100_000_000]
                admitted, release = threading.Event(), threading.Event()
                original = self.vm.call
                def delayed(fn_id, *args, **kwargs):
                    if fn_id == self.ids['retail.commit_request']:
                        admitted.set()
                        if not release.wait(3): raise RuntimeError('test release missing')
                    return original(fn_id, *args, **kwargs)
                # Isolate authority expiry from the real HTTP/socket deadline clock.
                socket_clock = time.monotonic_ns
                with patch('gopyt.resource_authority.time', SimpleNamespace(monotonic_ns=lambda: clock[0])):
                    self.assertIs(time.monotonic_ns, socket_clock)
                    self.provision('alice', 'alpha')
                    token = self.broker.issue('alice', 'alpha', ttl_ms=100)
                    changes = [{'key':'tenant/alpha/key','expected':'a','value':'bad'}]
                    with patch.object(self.vm, 'call', side_effect=delayed), \
                            concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        job = pool.submit(self.request, '/commit', {'changes':changes}, token)
                        try:
                            self.assertTrue(admitted.wait(3))
                            if mode == 'expiry': clock[0] += 100_000_000
                            else: self.broker.revoke('alice', 'alpha')
                        finally:
                            release.set()
                        self.assertEqual(job.result(), (200, {'outcome':'db_error'}))
                self.assertEqual(self.vm.db.get('tenant/alpha/key'), 'a')


class StrictHttpIdentities(HttpIdentities):
    """Repeat request boundaries with real encrypted storage and strict listener."""
    strict_profile = True

    def test_encrypted_tenant_data_remains_bound_to_verified_identity(self):
        from gopyt.storage import DIRECTORY, DATABASE
        value = 'tenant-alpha-private-data-unique'
        self.vm.db.put('tenant/alpha/private', value)
        self.assertNotIn(value.encode(), (self.root / DIRECTORY / DATABASE).read_bytes())
        payload = {'keys':['tenant/alpha/private']}
        self.assertEqual(self.request('/read', payload, self.alice), (200, {'outcome':'ok','values':[value]}))
        self.assertEqual(self.request('/read', payload, self.bob), (200, {'outcome':'db_error','values':[]}))


if __name__ == '__main__':
    unittest.main()
