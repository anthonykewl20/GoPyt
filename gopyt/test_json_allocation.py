"""Ordinary JSON conversion obeys the existing per-value allocation ceiling."""
import json
import tempfile
import unittest
from unittest.mock import patch

from gopyt import gobyte, jsonc, ops
from gopyt.cli import build, make_vm
from gopyt.testing import write_pkg, run_cli
from gopyt.vm import Trap


class JsonAllocation(unittest.TestCase):
    def test_failed_encoding_clears_output_retained_by_traceback(self):
        art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_STR)])
        for text in (False, True):
            with self.subTest(text=text):
                try:
                    if text:
                        with patch.object(ops, 'MAX_ALLOC', 20):
                            jsonc.encode(art, '\x00' * 10, 0)
                    else:
                        jsonc.encode_bytes(art, '\x00' * 10, 0, max_bytes=20)
                except (jsonc.ConvertFail, jsonc.AllocationLimit) as error:
                    cause = error.__cause__ or error
                    trace = cause.__traceback__
                    tokens = []
                    while trace is not None:
                        if trace.tb_frame.f_code.co_name == 'token':
                            tokens.append(trace.tb_frame.f_locals.get('raw'))
                        trace = trace.tb_next
                    self.assertEqual(tokens, [None])
                    trace = error.__traceback__
                    encoders = []
                    while trace is not None:
                        output = trace.tb_frame.f_locals.get('output')
                        if isinstance(output, jsonc._Encoder): encoders.append(output)
                        trace = trace.tb_next
                    self.assertTrue(encoders)
                    for encoder in encoders:
                        self.assertTrue(encoder.output.closed if text else len(encoder.output) == 0)
                else:
                    self.fail('encoding unexpectedly succeeded')

    def test_exact_escaped_utf8_boundary(self):
        art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_STR)])
        for value in ('', 'plain', '\x00\b\n\t"\\', 'é😀', 'x'*4095+'\n😀'+'y'*4100):
            expected = json.dumps(value, ensure_ascii=False)
            size = len(expected.encode('utf-8'))
            with self.subTest(size=size):
                with patch.object(ops, 'MAX_ALLOC', size):
                    self.assertEqual(jsonc.encode(art, value, 0), expected)
                with patch.object(ops, 'MAX_ALLOC', size-1), self.assertRaises(jsonc.AllocationLimit):
                    jsonc.encode(art, value, 0)

    def test_collection_preflight_and_canonical_output(self):
        art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_STR),
            gobyte.TExpr(gobyte.TE_I64), gobyte.TExpr(gobyte.TE_LIST, a=1),
            gobyte.TExpr(gobyte.TE_MAP, a=0, b=2)])
        value = {'é':[1,-2], 'a':[], '😀':[3]}
        expected = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        with patch.object(ops, 'MAX_ALLOC', len(expected.encode())):
            self.assertEqual(jsonc.encode(art, value, 3), expected)
        with patch.object(ops, 'MAX_ALLOC', len(expected.encode())-1), self.assertRaises(jsonc.AllocationLimit):
            jsonc.encode(art, value, 3)
        with patch.object(ops, 'MAX_ALLOC', 20):
            with patch('gopyt.jsonc.json.dumps', side_effect=AssertionError('escaped oversized string')):
                with self.assertRaises(jsonc.AllocationLimit):
                    jsonc.encode(art, 'x'*10000, 0)
            with patch('builtins.sorted', side_effect=AssertionError('sorted oversized keys')):
                with self.assertRaises(jsonc.AllocationLimit):
                    jsonc.encode(art, {str(i):[] for i in range(1000)}, 3)

    def test_compiled_native_and_generated_provide_trap_and_recover(self):
        with tempfile.TemporaryDirectory() as root:
            write_pkg(root, {
                'spec/demo.gopyt': '''module demo
use core.status { ConvertError }
fn render(value: str) -> str | ConvertError
fn via_trait(value: str) -> str | ConvertError
''',
                'impl/demo.gopyt': '''module demo
use core.status { ConvertError }
use core.convert { Json }
use data.json { encode }
fn render(value: str) -> str | ConvertError
{
    return data.json.encode(value)
}
fn via_trait(value: str) -> str | ConvertError
{
    return core.convert.Json.to_json(value)
}
''',
            }, fmt=True)
            prog, art, ids = build(root)
            vm = make_vm(root, prog, art, ids)
            for name in ('demo.render', 'demo.via_trait'):
                with self.subTest(name=name), patch.object(ops, 'MAX_ALLOC', 16):
                    for value in ('x'*15, '\x01'*3, 'é'*8):
                        with self.assertRaises(Trap) as caught:
                            vm.call(ids[name], [value])
                        self.assertEqual(caught.exception.code, 14)
                    self.assertEqual(vm.call(ids[name], ['x'*14]), '"'+'x'*14+'"')

    def test_cli_result_serialization_reports_allocation_trap(self):
        with tempfile.TemporaryDirectory() as root:
            write_pkg(root, {
                'spec/demo.gopyt': 'module demo\ntask main() -> str\n    effects { log }\n',
                'impl/demo.gopyt': '''module demo
use core.log { write }
task main() -> str
    effects { log }
{
    core.log.write("output")
    return "abcdefghijklmno"
}
''',
            }, fmt=True)
            with patch.object(ops, 'MAX_ALLOC', 16):
                code, output = run_cli(root, 'run', 'demo.main')
            self.assertEqual(code, 2)
            self.assertIn('GOPYT_E101', output)
            self.assertIn('trap: 14', output)
            self.assertNotIn('abcdefghijklmno', output)
            with patch.object(ops, 'MAX_ALLOC', 17):
                code, output = run_cli(root, 'run', 'demo.main')
            self.assertEqual(code, 0)
            self.assertTrue(output.endswith('"abcdefghijklmno"\n'), output)
