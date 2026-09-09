"""Delegation, revocation and real native resource boundaries through GoPyT."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from gopyt.cli import build
from gopyt.resource_authority import ResourceAuthority, AuthorityError, MAX_DEPTH
from gopyt.testing import write_pkg
from gopyt.values import Record, Some, UNIT
from gopyt.vm import VM


class Grants(unittest.TestCase):
    def test_attenuation_and_namespace_boundaries(self):
        parent = ResourceAuthority.issue(database_read=['tenant/'], file_read=['data/'],
                                         network=['https://example.com:443'])
        child = parent.delegate(database_read=['tenant/a/'], file_read=['data/a.txt'])
        self.assertTrue(child.admits([('database_read', 'tenant/a/x')]))
        self.assertFalse(child.admits([('database_read', 'tenant/ab/x')]))
        self.assertFalse(child.admits([('file_read', 'data/a.txt/other')]))
        for rights in ({'database_write': ['tenant/a/']}, {'database_read': ['*']},
                       {'file_read': ['data/']}, {'network': ['https://evil.test:443']}):
            with self.subTest(rights=rights), self.assertRaises(AuthorityError):
                child.delegate(**rights)

    def test_parent_revocation_and_sibling_independence(self):
        root = ResourceAuthority.issue(database_read=['*'])
        left = root.delegate(database_read=['a/'])
        right = root.delegate(database_read=['b/'])
        leaf = left.delegate(database_read=['a/child/'])
        left.revoke()
        self.assertFalse(leaf.admits([('database_read', 'a/child/key')]))
        self.assertTrue(right.admits([('database_read', 'b/key')]))
        with self.assertRaises(AuthorityError):
            left.delegate()
        root.revoke()
        self.assertFalse(right.admits([('database_read', 'b/key')]))

    def test_invalid_grants_and_bounded_delegation(self):
        for rights in ({'unknown': []}, {'file_read': ['../private/']},
                       {'file_read': ['a//']}, {'database_read': ['tenant']},
                       {'network': ['https://example.com/']}, {'secrets': ['API_KEY']},
                       {'listen': ['localhost:0']}, {'file_read': '*' }):
            with self.subTest(rights=rights), self.assertRaises(AuthorityError):
                ResourceAuthority.issue(**rights)
        grant = ResourceAuthority.issue()
        for _ in range(MAX_DEPTH):
            grant = grant.delegate()
        with self.assertRaises(AuthorityError):
            grant.delegate()


class ResourceCalls(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'app'; self.root.mkdir()
        spec_head = ('module probe\n\nuse core.status { DbError, NotFound, IoError, ModelError }\n'
                     'use store.db { Change }\n\n')
        signatures = {
            'read': 'task read(key: str) -> str | NotFound | DbError\n    effects { database.read }\n',
            'write': 'task write(key: str, value: str) -> unit | DbError\n    effects { database.write }\n',
            'batch': 'task batch(changes: list[Change]) -> bool | DbError\n    effects { database.read, database.write }\n',
            'file': 'task file(path: str) -> bytes | NotFound | IoError\n    effects { filesystem.read }\n',
            'save': 'task save(path: str, data: bytes) -> unit | IoError\n    effects { filesystem.write }\n',
            'secret': 'task secret(name: str) -> str | NotFound\n    effects { secret }\n',
            'local': 'task local() -> str | ModelError\n    effects { model }\n',
            'parallel': 'task parallel_read() -> list[str | NotFound | DbError]\n    effects { database.read, time }\n',
        }
        bodies = {
            'read': 'return deputy.read(key)',
            'write': 'return store.db.put(key, value)',
            'batch': 'return store.db.compare_exchange_many(changes)',
            'file': 'return core.file.read(path)',
            'save': 'return core.file.write(path, data)',
            'secret': ('found = core.secret.get(name)\n    return match found {\n'
                       '        Secret -> core.secret.reveal(found)\n        NotFound -> found\n    }'),
            'local': 'return core.model.local("probe", 1)',
            'parallel': ('return parallel max 2 timeout_ms 1000 {\n'
                         '        deputy.read("tenant/a/key")\n'
                         '        deputy.read("tenant/b/key")\n    }'),
        }
        deputy = ('module deputy\n\nuse core.status { DbError, NotFound }\n\n'
                  'task read(key: str) -> str | NotFound | DbError\n    effects { database.read }\n')
        write_pkg(str(self.root), {
            'spec/probe.gopyt': spec_head + '\n'.join(signatures.values()),
            'impl/probe.gopyt': spec_head.replace('use store.db { Change }', 'use store.db { Change, put, compare_exchange_many }') + ('use deputy { read }\n'
                'use core.file { read, write }\nuse core.model { local }\nuse core.secret { Secret, get, reveal }\n\n') + '\n'.join(
                    sig + '{\n    ' + bodies[name] + '\n}\n' for name, sig in signatures.items()),
            'spec/deputy.gopyt': deputy,
            'impl/deputy.gopyt': deputy.replace('task read', 'use store.db { get }\n\ntask read')
                                + '{\n    return store.db.get(key)\n}\n',
        }, fmt=True)
        _, self.art, self.ids = build(str(self.root))
        self.authority = ResourceAuthority.issue(database_read=['tenant/'], database_write=['tenant/'],
                                                  file_read=['data/'], file_write=['data/'], secrets=['tenant_a_token'])
        self.vm = VM(self.art, str(self.root), authority=self.authority)
        self.vm.db.put('tenant/a/key', 'a'); self.vm.db.put('tenant/b/key', 'b')
        (self.root / 'data').mkdir()
        (self.root / 'data/a.txt').write_bytes(b'a')
        (self.root / 'data/b.txt').write_bytes(b'b')

    def call(self, name, *args):
        return self.vm.call(self.ids['probe.' + name], list(args))

    def status(self, value, name):
        self.assertIsInstance(value, Record)
        self.assertEqual(self.vm.type_name(value.type_id), 'core.status.' + name)

    def test_warm_cache_and_transitive_deputy_cannot_expand_scope(self):
        self.assertEqual(self.call('read', 'tenant/b/key'), 'b')
        child = self.authority.delegate(database_read=['tenant/a/'])
        with self.vm.authority_scope(child):
            self.assertEqual(self.call('read', 'tenant/a/key'), 'a')
            self.status(self.call('read', 'tenant/b/key'), 'DbError')
            self.status(self.call('write', 'tenant/a/key', 'bad'), 'DbError')
        self.assertEqual(self.call('read', 'tenant/b/key'), 'b')

    def test_mixed_authority_batch_is_atomic(self):
        child = self.authority.delegate(database_read=['tenant/a/'], database_write=['tenant/a/'])
        tid = self.vm.type_id_of('store.db.Change')
        changes = [Record(tid, ['tenant/a/key', Some('a'), Some('changed')]),
                   Record(tid, ['tenant/b/key', Some('b'), Some('changed')])]
        with self.vm.authority_scope(child):
            self.status(self.call('batch', changes), 'DbError')
            for invalid in ([], [changes[0]] * 257):
                self.status(self.call('batch', invalid), 'DbError')
        self.assertEqual(self.vm.db.get_many(['tenant/a/key', 'tenant/b/key']), ['a', 'b'])

    def test_scope_propagates_to_parallel_arms_and_restores_after_exception(self):
        child = self.authority.delegate(database_read=['tenant/a/'])
        with self.assertRaises(RuntimeError):
            with self.vm.authority_scope(child):
                results = self.call('parallel_read')
                self.assertEqual(results[0], 'a'); self.status(results[1], 'DbError')
                raise RuntimeError('host scope unwind')
        self.assertEqual(self.call('parallel_read'), ['a', 'b'])

    def test_file_exact_grant_traversal_and_link_denial(self):
        child = self.authority.delegate(file_read=['data/a.txt'], file_write=['data/a.txt'])
        with self.vm.authority_scope(child):
            self.assertEqual(self.call('file', 'data/a.txt'), b'a')
            self.status(self.call('file', 'data/b.txt'), 'IoError')
            self.status(self.call('save', 'data/b.txt', b'bad'), 'IoError')
            self.status(self.call('file', 'data/../gopyt.toml'), 'IoError')
            (self.root / 'data/a.txt').unlink()
            (self.root / 'data/a.txt').symlink_to(self.root / 'data/b.txt')
            self.status(self.call('file', 'data/a.txt'), 'IoError')
        self.assertEqual((self.root / 'data/b.txt').read_bytes(), b'b')

    def test_revocation_precedes_cached_access(self):
        self.assertEqual(self.call('read', 'tenant/a/key'), 'a')
        self.authority.revoke()
        self.status(self.call('read', 'tenant/a/key'), 'DbError')
        self.status(self.call('save', 'data/a.txt', b'bad'), 'IoError')
        self.assertEqual((self.root / 'data/a.txt').read_bytes(), b'a')

    def test_file_replacement_after_admission_cannot_follow_link(self):
        from gopyt.files import regular_file
        target = self.root / 'data/a.txt'
        outside = self.base / 'outside'; outside.write_bytes(b'private')
        def replace_then_open(*args, **kwargs):
            target.unlink(); target.symlink_to(outside)
            return regular_file(*args, **kwargs)
        with patch('gopyt.files.regular_file', side_effect=replace_then_open):
            self.status(self.call('save', 'data/a.txt', b'bad'), 'IoError')
        self.assertEqual(outside.read_bytes(), b'private')

    def test_simultaneous_host_scopes_do_not_share_authority(self):
        barrier = threading.Barrier(2)
        failures = []
        def reader(tenant, other):
            grant = self.authority.delegate(database_read=['tenant/' + tenant + '/'])
            try:
                with self.vm.authority_scope(grant):
                    barrier.wait(timeout=3)
                    for _ in range(20):
                        self.assertEqual(self.call('read', 'tenant/' + tenant + '/key'), tenant)
                        self.status(self.call('read', 'tenant/' + other + '/key'), 'DbError')
            except BaseException as exc:
                failures.append(exc)
        threads = [threading.Thread(target=reader, args=pair) for pair in [('a', 'b'), ('b', 'a')]]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])

    def test_revoke_does_not_abort_an_admitted_commit(self):
        admitted, release = threading.Event(), threading.Event()
        original = self.vm.db._save
        def save(*args):
            admitted.set()
            if not release.wait(3):
                raise RuntimeError('test did not release write')
            return original(*args)
        result = []
        def writer():
            try:
                result.append(self.call('write', 'tenant/a/key', 'committed'))
            except BaseException as exc:
                result.append(exc)
        with patch.object(self.vm.db, '_save', side_effect=save):
            thread = threading.Thread(target=writer); thread.start()
            try:
                self.assertTrue(admitted.wait(3))
                self.authority.revoke()
            finally:
                release.set(); thread.join(3)
        self.assertFalse(thread.is_alive()); self.assertEqual(result, [UNIT])
        self.assertEqual(self.vm.db.get('tenant/a/key'), 'committed')
        self.status(self.call('read', 'tenant/a/key'), 'DbError')

    def test_unmediated_provider_never_imported_and_forged_scope_refused(self):
        def unexpected(*args):
            raise AssertionError('unmediated provider entered')
        with patch.dict(self.vm.natives, {'core.model.local': unexpected}):
            self.status(self.call('local'), 'ModelError')
        with self.assertRaises(TypeError):
            VM(self.art, str(self.root), authority={'database_read': ['*']})
        for forged in (None, {}, ResourceAuthority.issue(database_read=['*'])):
            with self.subTest(forged=type(forged)), self.assertRaises(AuthorityError):
                with self.vm.authority_scope(forged):
                    pass

    def test_secret_acquisition_is_scoped_and_revocable(self):
        with patch.dict(os.environ, {'GOPYT_SECRET_TENANT_A_TOKEN': 'synthetic-a',
                                     'GOPYT_SECRET_TENANT_B_TOKEN': 'synthetic-b'}):
            self.assertEqual(self.call('secret', 'tenant_a_token'), 'synthetic-a')
            self.status(self.call('secret', 'tenant_b_token'), 'NotFound')
            self.authority.revoke()
            self.status(self.call('secret', 'tenant_a_token'), 'NotFound')

    def test_operator_policy_intersects_delegated_grants(self):
        policy = self.base / 'policy.json'
        policy.write_text(json.dumps({'version': 1, 'read': ['tenant/b/'], 'write': []}))
        policy.chmod(0o600)
        with patch.dict(os.environ, {'GOPYT_DB_POLICY_FILE': str(policy)}):
            self.status(self.call('read', 'tenant/a/key'), 'DbError')
            self.assertEqual(self.call('read', 'tenant/b/key'), 'b')


class NetworkCalls(unittest.TestCase):
    def setUp(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.hits = []
        hits = self.hits
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                body = b'{"text":"local response"}'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                self.do_GET()
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = str(Path(temporary.name).resolve())
        head = ('module remote\n\nuse core.status { HttpError, ModelError }\n'
                'use net.http { HttpRequest, HttpResponse }\n\n')
        fetch = 'task fetch(req: HttpRequest) -> HttpResponse | HttpError\n    effects { network }\n'
        model = 'task complete() -> str | ModelError\n    effects { model, network }\n'
        write_pkg(self.root, {
            'spec/remote.gopyt': head + 'egress { "' + self.origin + '" }\n\n' + fetch + '\n' + model,
            'impl/remote.gopyt': head.replace('HttpRequest, HttpResponse', 'HttpRequest, HttpResponse, request')
                + 'use core.model { complete }\n\n' + fetch + '{\n    return net.http.request(req)\n}\n\n'
                + model + '{\n    return core.model.complete("probe")\n}\n',
        }, fmt=True)
        _, art, self.ids = build(self.root)
        self.authority = ResourceAuthority.issue(network=[self.origin])
        self.vm = VM(art, self.root, authority=self.authority)

    def request(self, url):
        from gopyt.values import EnumVal
        method = EnumVal(self.vm.type_id_of('net.http.HttpMethod'), 0, [])
        req = Record(self.vm.type_id_of('net.http.HttpRequest'), [method, url, b''])
        return self.vm.call(self.ids['remote.fetch'], [req])

    def test_real_http_and_model_egress_require_operator_and_source_grants(self):
        response = self.request(self.origin + '/allowed')
        self.assertEqual(response.fields[0], 200)
        self.assertEqual(self.hits, ['/allowed'])
        # A source-declared allowed origin cannot elevate an attenuated scope.
        with self.vm.authority_scope(self.authority.delegate()):
            denied = self.request(self.origin + '/denied')
            self.assertEqual(self.vm.type_name(denied.type_id), 'core.status.HttpError')
            with patch.dict(os.environ, {'GOPYT_MODEL_URL': self.origin + '/model-denied'}):
                denied = self.vm.call(self.ids['remote.complete'], [])
            self.assertEqual(self.vm.type_name(denied.type_id), 'core.status.ModelError')
        self.assertEqual(self.hits, ['/allowed'])
        with patch.dict(os.environ, {'GOPYT_MODEL_URL': self.origin + '/model'}):
            self.assertEqual(self.vm.call(self.ids['remote.complete'], []), 'local response')
        self.authority.revoke()
        denied = self.request(self.origin + '/revoked')
        self.assertEqual(self.vm.type_name(denied.type_id), 'core.status.HttpError')
        self.assertEqual(self.hits, ['/allowed', '/model'])

    def test_operator_grant_does_not_replace_source_egress(self):
        broad = ResourceAuthority.issue(network=['*'])
        vm = VM(self.vm.art, self.root, authority=broad)
        with patch('urllib.request.build_opener', side_effect=AssertionError('network reached')):
            from gopyt.values import EnumVal
            req = Record(vm.type_id_of('net.http.HttpRequest'),
                         [EnumVal(vm.type_id_of('net.http.HttpMethod'), 0, []),
                          'http://unlisted.invalid/private', b''])
            denied = vm.call(self.ids['remote.fetch'], [req])
        self.assertEqual(vm.type_name(denied.type_id), 'core.status.HttpError')


class ServingAuthority(unittest.TestCase):
    def test_listener_and_worker_inherit_attenuated_scope(self):
        import socket
        import time
        import urllib.request
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = str(Path(temporary.name).resolve())
        head = 'module service\n\nuse core.status { ListenError, DbError, NotFound }\n\n'
        read = 'task read(key: str) -> str\n    effects { database.read }\n'
        serve = 'task serve() -> unit | ListenError\n    effects { network, database.read }\n'
        write_pkg(root, {
            'spec/service.gopyt': head.replace('ListenError, DbError, NotFound', 'ListenError') + 'http { get "/{key}" read }\n\n' + read + '\n' + serve,
            'impl/service.gopyt': head + 'use store.db { get }\nuse net.http { serve }\n\n' + read
                + '{\n    result = store.db.get(key)\n    return match result {\n'
                  '        str -> result\n        NotFound -> "missing"\n'
                  '        DbError { message } -> "denied"\n    }\n}\n\n'
                + serve + '{\n    return net.http.serve()\n}\n',
        }, fmt=True)
        _, art, ids = build(root)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        address = '127.0.0.1:' + str(port)
        parent = ResourceAuthority.issue(listen=[address], database_read=['*'])
        child = parent.delegate(listen=[address])
        vm = VM(art, root, authority=parent)
        vm.db.put('private', 'sensitive')
        result = []
        def run():
            try:
                with vm.authority_scope(child):
                    result.append(vm.call(ids['service.serve'], []))
            except BaseException as exc:
                result.append(exc)
        with patch.dict(os.environ, {'GOPYT_HTTP_ADDR': address}), patch('gopyt.server.MAX_HANDLERS', 2):
            denied_vm = VM(art, root, authority=ResourceAuthority.issue())
            denied = denied_vm.call(ids['service.serve'], [])
            self.assertEqual(vm.type_name(denied.type_id), 'core.status.ListenError')
            self.assertFalse(denied_vm.serving)
            thread = threading.Thread(target=run); thread.start()
            try:
                deadline = time.monotonic() + 3
                while getattr(vm, 'httpd', None) is None and not result and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertIsNotNone(getattr(vm, 'httpd', None), result)
                with urllib.request.urlopen('http://' + address + '/private', timeout=3) as response:
                    self.assertEqual(json.loads(response.read()), 'denied')
            finally:
                if getattr(vm, 'httpd', None) is not None:
                    vm.httpd.shutdown()
                thread.join(3)
            self.assertFalse(thread.is_alive()); self.assertEqual(result, [UNIT])


class EvolutionAuthority(unittest.TestCase):
    def test_restricted_vm_never_enters_evolution_host_code(self):
        from gopyt.test_evolve import ThroughTheVm
        from gopyt.manifest import package_digest
        fixture = ThroughTheVm(); fixture.setUp(); self.addCleanup(fixture.tearDown)
        _, art, ids = build(fixture.root)
        before = package_digest(fixture.root)
        vm = VM(art, fixture.root, authority=ResourceAuthority.issue(
            database_read=['*'], file_read=['*'], file_write=['*'], network=['*']))
        def unexpected(*args):
            raise AssertionError('unmediated evolution entered')
        with patch.dict(vm.natives, {'core.evolve.propose': unexpected}):
            result = vm.call(ids['auth.harden'], [])
        self.assertEqual(vm.type_name(result.type_id), 'core.evolve.EvolveError')
        self.assertEqual(package_digest(fixture.root), before)


if __name__ == '__main__':
    unittest.main()
