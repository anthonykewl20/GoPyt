"""All integer-width conversions through compiled calls and explicit boundaries."""
import tempfile
import unittest

from gopyt.cli import build
from gopyt.diag import CompileError
from gopyt.testing import write_pkg
from gopyt.values import I32, U32, U64, Record
from gopyt.vm import VM


class NumericConversions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        conversions = {'to_i32': ('i64', 'i32 | ConvertError'),
                       'to_u32': ('i64', 'u32 | ConvertError'),
                       'to_u64': ('i64', 'u64 | ConvertError'),
                       'to_i64_from_i32': ('i32', 'i64'),
                       'to_i64_from_u32': ('u32', 'i64'),
                       'to_i64_from_u64': ('u64', 'i64 | ConvertError')}
        spec = 'module demo\n\nuse core.status { ConvertError }\n\n'
        impl = spec + 'use core.int { ' + ', '.join(conversions) + ' }\n\n'
        for name, (arg, ret) in conversions.items():
            decl = f'fn {name}(value: {arg}) -> {ret}\n'
            spec += decl + '\n'
            impl += decl + '{\n    return core.int.' + name + '(value)\n}\n\n'
        write_pkg(cls.temp.name, {'spec/demo.gopyt': spec, 'impl/demo.gopyt': impl}, fmt=True)
        cls.art = build(cls.temp.name)[1]

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.vm = VM(self.art, self.temp.name)

    def call(self, name, value):
        return self.vm.call(self.vm.by_name['demo.' + name], [value])

    def test_i64_to_width_boundaries_preserve_value_and_tag(self):
        for name, low, high, tag in [('to_i32', -2**31, 2**31-1, I32),
                                      ('to_u32', 0, 2**32-1, U32),
                                      ('to_u64', 0, 2**64-1, U64)]:
            candidates = {-2**63, -1, 0, 1, 2**63-1, low, low-1, high, high+1}
            for value in sorted(v for v in candidates if -2**63 <= v < 2**63):
                with self.subTest(name=name, value=value):
                    result = self.call(name, value)
                    if low <= value <= high:
                        self.assertIs(type(result), tag)
                        self.assertEqual(int(result), value)
                    else:
                        self.assertIsInstance(result, Record)
                        self.assertEqual(self.vm.type_name(result.type_id), 'core.status.ConvertError')

    def test_every_width_to_i64_boundary_is_exact_or_rejected(self):
        for tag, values, name in [(I32, [-2**31, -1, 0, 2**31-1], 'i32'),
                                   (U32, [0, 1, 2**32-1], 'u32'),
                                   (U64, [0, 1, 2**63-1, 2**63, 2**64-1], 'u64')]:
            for value in values:
                with self.subTest(name=name, value=value):
                    result = self.call('to_i64_from_' + name, tag(value))
                    if value <= 2**63-1:
                        self.assertIs(type(result), int)
                        self.assertEqual(result, value)
                    else:
                        self.assertIsInstance(result, Record)
                        self.assertEqual(self.vm.type_name(result.type_id), 'core.status.ConvertError')

    def test_no_implicit_numeric_width_or_float_conversion(self):
        for source, target in [('i64', 'i32'), ('i64', 'u32'), ('i64', 'u64'),
                               ('i32', 'i64'), ('u32', 'i64'), ('u64', 'i64'),
                               ('i64', 'f64'), ('f64', 'i64')]:
            with self.subTest(source=source, target=target), tempfile.TemporaryDirectory() as root:
                decl = f'fn convert(value: {source}) -> {target}\n'
                write_pkg(root, {'spec/demo.gopyt': 'module demo\n\n' + decl,
                                 'impl/demo.gopyt': 'module demo\n\n' + decl + '{\n    return value\n}\n'}, fmt=True)
                with self.assertRaises(CompileError) as caught:
                    build(root)
                self.assertEqual(caught.exception.diag.code, 21)
