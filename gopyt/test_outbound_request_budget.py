"""Outbound payload bounds reject before transport admission."""
import json
import os
import threading
import time
import unittest
from unittest.mock import patch

from gopyt import jsonc, natives
from gopyt import test_resource_authority as fixtures
from gopyt.values import EnumVal, Record
from gopyt.vm import Cancelled, Trap


class OutboundRequestBudget(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NetworkCalls()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.vm = self.fixture.vm
        self.vm.natives = dict(self.vm.natives)
        self.bodies = []
        bodies = self.bodies
        handler = self.fixture.server.RequestHandlerClass
        def post(request):
            bodies.append(request.rfile.read(int(request.headers.get('Content-Length', '0'))))
            request.do_GET()
        self.patch = patch.object(handler, 'do_POST', post)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def http(self, body=b'', url=None):
        method = EnumVal(self.vm.type_id_of('net.http.HttpMethod'), 1, [])
        req = Record(self.vm.type_id_of('net.http.HttpRequest'),
                     [method, url or self.fixture.origin + '/body', body])
        return self.vm.call(self.fixture.ids['remote.fetch'], [req])

    def model(self, prompt, url=None):
        original = self.vm.natives['core.model.complete']
        def invoke(vm, args, func):
            return original(vm, [prompt], func)
        with patch.dict(self.vm.natives, {'core.model.complete': invoke}), \
                patch.dict(os.environ, {'GOPYT_MODEL_URL': url or self.fixture.origin + '/model'}):
            return self.vm.call(self.fixture.ids['remote.complete'], [])

    def test_http_exact_body_limit_and_rejection_before_transport(self):
        body = b'x' * 1_048_576
        response = self.http(body)
        self.assertEqual(response.fields[0], 200)
        self.assertEqual(self.bodies, [body])
        with patch('gopyt.netio.opener') as opener:
            rejected = self.http(body + b'x')
        opener.assert_not_called()
        self.assertEqual(self.vm.type_name(rejected.type_id), 'core.status.HttpError')
        self.assertEqual(rejected.fields, ['body too large'])
        self.assertEqual(len(self.bodies), 1)

    def test_model_full_payload_limit_includes_json_envelope(self):
        prompt = 'x' * (1_048_576 - 13)
        self.assertEqual(self.model(prompt), 'local response')
        self.assertEqual(len(self.bodies[-1]), 1_048_576)
        self.assertEqual(json.loads(self.bodies[-1]), {'prompt': prompt})
        with patch('gopyt.netio.opener') as opener:
            rejected = self.model(prompt + 'x')
        opener.assert_not_called()
        self.assertEqual(self.vm.type_name(rejected.type_id), 'core.status.ModelError')
        self.assertEqual(rejected.fields, ['body too large'])
        self.assertEqual(len(self.bodies), 1)

    def test_model_limit_counts_escaped_utf8_bytes(self):
        prompt = '\x00\n"\\é😀'
        expected = json.dumps({'prompt': prompt}, ensure_ascii=False, separators=(',', ':')).encode()
        with patch('gopyt.natives.MAX_REQUEST_BODY', len(expected)):
            self.assertEqual(self.model(prompt), 'local response')
        self.assertEqual(self.bodies, [expected])
        with patch('gopyt.natives.MAX_REQUEST_BODY', len(expected)-1), \
                patch('gopyt.netio.opener') as opener:
            self.assertEqual(self.model(prompt).fields, ['body too large'])
        opener.assert_not_called()

    def test_url_byte_limit_precedes_transport_for_http_and_model(self):
        url = self.fixture.origin + '/' + 'x' * (8192-len(self.fixture.origin)-1)
        self.assertEqual(self.http(url=url).fields[0], 200)
        self.assertEqual(len(self.fixture.hits), 1)
        for value in (url+'x', self.fixture.origin+'/'+'é'*4096):
            for invoke in (lambda: self.http(url=value), lambda: self.model('probe', url=value)):
                with self.subTest(url_length=len(value)), patch('gopyt.netio.opener') as opener:
                    result = invoke()
                    self.assertEqual(result.fields, ['url too large'])
                    opener.assert_not_called()
        self.assertEqual(len(self.fixture.hits), 1)

    def test_model_serialization_consumes_own_and_inherited_budgets(self):
        for condition in ('own', 'deadline', 'cancel'):
            clock = [time.monotonic_ns()]
            cancel = threading.Event()
            self.vm.cancels = (cancel,)
            self.vm.deadline_ns = clock[0] + 100_000_000 if condition == 'deadline' else None
            original = jsonc.encode_bytes
            def encode(*args, **kwargs):
                if condition == 'cancel':
                    cancel.set()
                else:
                    clock[0] += 101_000_000
                return original(*args, **kwargs)
            try:
                with self.subTest(condition=condition), \
                        patch('time.monotonic_ns', side_effect=lambda: clock[0]), \
                        patch('gopyt.natives.HTTP_TIMEOUT_MS', 100 if condition == 'own' else 30000), \
                        patch('gopyt.jsonc.encode_bytes', encode), patch('gopyt.netio.opener') as opener:
                    if condition == 'own':
                        result = self.model('probe')
                        self.assertEqual(self.vm.type_name(result.type_id), 'core.status.ModelError')
                        self.assertEqual(result.fields, ['network'])
                    else:
                        with self.assertRaises(Cancelled if condition == 'cancel' else Trap) as error:
                            self.model('probe')
                        if condition == 'deadline':
                            self.assertEqual(error.exception.code, 6)
                    opener.assert_not_called()
            finally:
                cancel.clear()
                self.vm.deadline_ns = None
        self.assertEqual(self.bodies, [])
        self.assertEqual(self.model('recovered'), 'local response')
