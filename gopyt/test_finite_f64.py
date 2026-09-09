"""Finite binary64 boundaries, using exact decimal construction and raw bits."""
import copy
from decimal import Decimal, localcontext
import math
from pathlib import Path
import struct
import tempfile
import unittest

from gopyt import gobyte, ops
from gopyt.cli import build
from gopyt.diag import CompileError
from gopyt.testing import write_pkg
from gopyt.values import Record, Some
from gopyt.vm import VM, Trap, require_finite_values


class FiniteF64(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name

    def literal(self, text):
        write_pkg(self.root, {'spec/demo.gopyt': 'module demo\n\nfn value() -> f64\n',
            'impl/demo.gopyt': 'module demo\n\nfn value() -> f64\n{\n    return ' + text + '\n}\n'})
        return build(self.root)[1]

    def test_overflow_literal_is_diagnostic_and_preserves_old_artifact(self):
        self.literal('1.0')
        path = Path(self.root, 'build/out.gobyte')
        before = path.read_bytes()
        with self.assertRaises(CompileError) as caught:
            self.literal('9' * 400 + '.0')
        self.assertEqual(caught.exception.diag.code, 118)
        self.assertEqual(path.read_bytes(), before)

    def test_rounding_subnormal_underflow_and_maximum_bits(self):
        with localcontext() as context:
            context.prec = 1800
            tiny = Decimal(2) ** -1074
            half = Decimal(2) ** -1075
            cases = [('0.0', 0), ('0.1', 0x3fb999999999999a),
                (str((2**53 - 1) * 2**971) + '.0', 0x7fefffffffffffff),
                (format(tiny, 'f'), 1), (format(half, 'f'), 0),
                (format(half + Decimal(2) ** -1100, 'f'), 1)]
        for source, bits in cases:
            with self.subTest(bits=bits):
                art = self.literal(source)
                vm = VM(art, self.root)
                value = vm.call(vm.by_name['demo.value'], [])
                self.assertEqual(struct.unpack('<Q', struct.pack('<d', value))[0], bits)

    def test_invalid_in_memory_constants_rejected(self):
        art = self.literal('1.0')
        index = next(i for i, c in enumerate(art.consts) if c.tag == gobyte.TAG_F64)
        for value in (float('nan'), float('inf'), -float('inf'), 1):
            for operation in (gobyte.encode, gobyte.validate, lambda a: VM(a, self.root)):
                with self.subTest(value=value, operation=operation):
                    bad = copy.deepcopy(art)
                    bad.consts[index].value = value
                    with self.assertRaises(CompileError) as caught:
                        operation(bad)
                    self.assertEqual(caught.exception.diag.code, 100)

    def test_raw_nonfinite_bits_rejected_including_unused_constants(self):
        art = self.literal('1.0')
        art.consts.append(gobyte.Const(gobyte.TAG_F64, 234.125))
        data = gobyte.encode(art)
        marker = bytes([gobyte.TAG_F64]) + struct.pack('<d', 234.125)
        self.assertEqual(data.count(marker), 1)
        for bits in (0x7ff0000000000000, 0xfff0000000000000,
                     0x7ff8000000000001, 0x7ff0000000000001):
            changed = data.replace(marker, bytes([gobyte.TAG_F64]) + struct.pack('<Q', bits))
            with self.assertRaises(CompileError) as caught:
                gobyte.decode(changed)
            self.assertEqual(caught.exception.diag.code, 100)

    def test_host_graphs_are_checked_each_call(self):
        write_pkg(self.root, {'spec/demo.gopyt': 'module demo\n\nfn echo(values: list[f64]) -> list[f64]\n',
            'impl/demo.gopyt': 'module demo\n\nfn echo(values: list[f64]) -> list[f64]\n{\n    return values\n}\n'})
        vm = VM(build(self.root)[1], self.root)
        values = [1.0, -0.0, -math.ulp(0.0)]
        self.assertIs(vm.call(vm.by_name['demo.echo'], [values]), values)
        self.assertEqual(math.copysign(1, values[1]), -1)
        values.append(float('nan'))  # Already admitted containers must be rechecked.
        with self.assertRaises(Trap) as caught:
            vm.call(vm.by_name['demo.echo'], [values])
        self.assertEqual(caught.exception.code, ops.TRAP_TYPE)

    def test_cycle_and_nested_values_do_not_bypass_check(self):
        graph = []
        graph.append(graph)
        require_finite_values(graph)
        graph.append(Record(0, [Some({'nested': float('inf')})]))
        with self.assertRaises(Trap) as caught:
            require_finite_values(graph)
        self.assertEqual(caught.exception.code, ops.TRAP_TYPE)

    def test_native_nonfinite_result_is_type_trap(self):
        write_pkg(self.root, {'spec/demo.gopyt': 'module demo\n\ntask now() -> i64\n    effects { time }\n',
            'impl/demo.gopyt': 'module demo\n\nuse core.time { now_ms }\n\ntask now() -> i64\n    effects { time }\n{\n    return core.time.now_ms()\n}\n'})
        vm = VM(build(self.root)[1], self.root)
        vm.natives = dict(vm.natives)
        vm.natives['core.time.now_ms'] = lambda *args: float('nan')
        with self.assertRaises(Trap) as caught:
            vm.call(vm.by_name['demo.now'], [])
        self.assertEqual(caught.exception.code, ops.TRAP_TYPE)
