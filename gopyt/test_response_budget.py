"""Bounded canonical encoding and real HTTP rejection/recovery."""
import http.client
import json
import threading
import unittest
from unittest.mock import patch

from gopyt import gobyte, jsonc
from gopyt import test_app_runtime as fixture
from gopyt.vm import Trap


class ResponseBudget(unittest.TestCase):
    def test_exact_utf8_and_escape_limits_match_independent_json(self):
        art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_STR)])
        for value in ('', 'plain', '\x00\b\n\t"\\', 'é😀', 'x'*4095+'\n😀'+'y'*4100):
            with self.subTest(length=len(value)):
                expected = json.dumps(value, ensure_ascii=False).encode('utf-8')
                self.assertEqual(jsonc.encode(art, value, 0).encode('utf-8'), expected)
                self.assertEqual(jsonc.encode_bytes(art, value, 0, max_bytes=len(expected)), expected)
                with self.assertRaises(jsonc.ConvertFail) as error:
                    jsonc.encode_bytes(art, value, 0, max_bytes=len(expected)-1)
                self.assertEqual(error.exception.message, 'size')

    def test_sorted_map_and_list_canonical_output(self):
        art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_STR),
            gobyte.TExpr(gobyte.TE_I64), gobyte.TExpr(gobyte.TE_LIST, a=1),
            gobyte.TExpr(gobyte.TE_MAP, a=0, b=2)])
        value = {'é':[1,-2], 'a':[], '😀':[3]}
        expected = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        self.assertEqual(jsonc.encode_bytes(art, value, 3, max_bytes=len(expected)), expected)
        self.assertEqual(jsonc.encode(art, value, 3).encode(), expected)
        with self.assertRaises(jsonc.ConvertFail):
            jsonc.encode_bytes(art, value, 3, max_bytes=len(expected)-1)

    def test_oversized_container_rejected_before_sort_or_escape(self):
        art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_STR),
            gobyte.TExpr(gobyte.TE_MAP, a=0, b=0)])
        with patch('gopyt.jsonc.json.dumps', side_effect=AssertionError('escaped oversized input')):
            with self.assertRaises(jsonc.ConvertFail):
                jsonc.encode_bytes(art, 'x'*10000, 0, max_bytes=20)
            with self.assertRaises(jsonc.ConvertFail):
                jsonc.encode_bytes(art, {'x'*10000:''}, 1, max_bytes=20)
        with patch('builtins.sorted', side_effect=AssertionError('sorted oversized keys')):
            with self.assertRaises(jsonc.ConvertFail):
                jsonc.encode_bytes(art, {str(i):'' for i in range(1000)}, 1, max_bytes=20)

    def test_depth_and_cancellation_bound_traversal(self):
        art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_LIST, a=0)])
        value = []
        for _ in range(127):
            value = [value]
        self.assertEqual(len(jsonc.encode_bytes(art, value, 0, max_bytes=1000)), 256)
        with self.assertRaises(jsonc.ConvertFail) as error:
            jsonc.encode_bytes(art, [value], 0, max_bytes=1000)
        self.assertEqual(error.exception.message, 'depth')
        art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_STR)])
        calls = 0
        def cancel():
            nonlocal calls
            calls += 1
            if calls == 4:
                raise Trap(6)
        with self.assertRaises(Trap) as error:
            jsonc.encode_bytes(art, 'x'*10000, 0, max_bytes=20000, check_context=cancel)
        self.assertEqual(error.exception.code, 6)
        self.assertEqual(calls, 4)

    def test_http_cap_rejects_without_success_headers_and_recovers(self):
        # The ordinary typed response is exactly 12 bytes: {"amount":3}.
        for cap, expected in ((12, (200, b'{"amount":3}')), (11, (500, b''))):
            with self.subTest(cap=cap), fixture.running_server(MAX_HANDLERS=1, MAX_RESPONSE_BODY=cap) as (vm, port):
                released = threading.Event()
                original_release = vm.heap.release_result
                def release_result():
                    original_release()
                    released.set()
                vm.heap.release_result = release_result
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                try:
                    for _ in range(2):
                        released.clear()
                        connection.request('GET', '/echo/abc')
                        response = connection.getresponse()
                        self.assertEqual((response.status, response.read()), expected)
                        self.assertEqual(response.getheader('Content-Length'), str(len(expected[1])))
                        # Receiving bytes does not join the handler's finally block.
                        self.assertTrue(released.wait(2), 'handler did not release its result')
                        self.assertEqual(vm.heap.handoffs, {})
                finally:
                    connection.close()
                self.assertEqual(vm.heap.handoffs, {})

    def test_http_serialization_expiry_sends_no_success_response(self):
        with fixture.running_server(MAX_HANDLERS=1) as (vm, port):
            original = jsonc.encode_owned_bytes
            def encode(*args, **kwargs):
                vm.deadline_ns = 1
                return original(*args, **kwargs)
            with patch('gopyt.server.jsonc.encode_owned_bytes', encode):
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                try:
                    connection.request('GET', '/echo/abc')
                    with self.assertRaises(http.client.RemoteDisconnected):
                        connection.getresponse()
                finally:
                    connection.close()
            self.assertEqual(fixture.request(port), (200, b'{"amount":3}'))
