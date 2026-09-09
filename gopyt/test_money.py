"""Compiled money API against an independent Decimal arithmetic oracle."""
from decimal import (Decimal, localcontext, ROUND_HALF_EVEN, ROUND_HALF_UP,
                     ROUND_DOWN, ROUND_FLOOR, ROUND_CEILING, Inexact)
import random
import tempfile
import unittest

from gopyt.cli import build
from gopyt.testing import write_pkg
from gopyt.values import Record, EnumVal
from gopyt.vm import VM


class MoneyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        signatures = {
            'make': 'units: i64, scale: i64, currency: str',
            'parse': 'text: str, scale: i64, currency: str, rounding: Rounding',
            'format': 'value: Money',
            'add': 'left: Money, right: Money',
            'subtract': 'left: Money, right: Money',
            'compare': 'left: Money, right: Money',
            'rescale': 'value: Money, scale: i64, rounding: Rounding',
            'multiply_ratio': 'value: Money, numerator: i64, denominator: i64, rounding: Rounding',
        }
        spec = impl = 'module demo\n\nuse core.money { Money, Rounding, make, parse, format, add, subtract, compare, rescale, multiply_ratio }\nuse core.status { ConvertError }\n\n'
        spec = spec.replace(', make, parse, format, add, subtract, compare, rescale, multiply_ratio', '')
        for name, signature in signatures.items():
            ret = {'format': 'str', 'compare': 'i64'}.get(name, 'Money')
            decl = f'fn {name}({signature}) -> {ret} | ConvertError\n'
            args = ', '.join(arg.split(':')[0] for arg in signature.split(', '))
            spec += decl + '\n'
            impl += decl + '{\n    return core.money.' + name + '(' + args + ')\n}\n\n'
        write_pkg(cls.temp.name, {'spec/demo.gopyt': spec, 'impl/demo.gopyt': impl}, fmt=True)
        cls.art = build(cls.temp.name)[1]

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.vm = VM(self.art, self.temp.name)

    def call(self, name, *args):
        return self.vm.call(self.vm.by_name['demo.' + name], list(args))

    def mode(self, variant):
        return EnumVal(self.vm.type_id_of('core.money.Rounding'), variant, [])

    def money(self, units, scale=2, currency='GBP'):
        return Record(self.vm.type_id_of('core.money.Money'), [units, scale, currency])

    def error(self, result):
        self.assertIsInstance(result, Record)
        self.assertEqual(self.vm.type_name(result.type_id), 'core.status.ConvertError')

    def units(self, result, expected, scale=2, currency='GBP'):
        self.assertEqual(self.vm.type_name(result.type_id), 'core.money.Money')
        self.assertEqual(result.fields, [expected, scale, currency])

    def test_parse_format_and_integer_extremes(self):
        for units, scale in [(0, 0), (0, 18), (-1, 18), (2**63 - 1, 0), (-2**63, 18), (235, 2)]:
            value = self.call('make', units, scale, 'GBP')
            text = self.call('format', value)
            result = self.call('parse', text, scale, 'GBP', self.mode(0))
            self.units(result, units, scale)
        self.units(self.call('parse', '-0.005', 2, 'GBP', self.mode(2)), -1)
        self.units(self.call('parse', '1.2300', 2, 'GBP', self.mode(0)), 123)
        self.assertEqual(self.call('format', self.money(120)), '1.20')

    def test_invalid_inputs_return_convert_error(self):
        for text in ['NaN', 'Infinity', '1e2', '+1', ' 1', '1 ', '01', '.1', '1.', '１', '1_0', '1\n',
                     '1'*81, '0.'+'1'*37]:
            self.error(self.call('parse', text, 2, 'GBP', self.mode(1)))
        for scale in [-1, 19, 2**63 - 1]:
            self.error(self.call('make', 1, scale, 'GBP'))
        for currency in ['', 'gbp', 'GB', 'GBPP', 'GßP', 'GBP\n']:
            self.error(self.call('make', 1, 2, currency))
        self.error(self.call('parse', '9223372036854775808', 0, 'GBP', self.mode(0)))
        self.error(self.call('parse', '0.001', 2, 'GBP', self.mode(0)))
        self.error(self.call('multiply_ratio', self.money(1), 1, 0, self.mode(1)))

    def test_mismatch_and_overflow_are_explicit(self):
        for operation in ['add', 'subtract', 'compare']:
            self.error(self.call(operation, self.money(1), self.money(1, currency='USD')))
            self.error(self.call(operation, self.money(1), self.money(1, scale=3)))
        self.error(self.call('add', self.money(2**63-1), self.money(1)))
        self.error(self.call('subtract', self.money(-2**63), self.money(1)))
        self.error(self.call('rescale', self.money(2**63-1, 0), 18, self.mode(0)))
        self.error(self.call('multiply_ratio', self.money(-2**63), -1, 1, self.mode(0)))
        self.units(self.call('multiply_ratio', self.money(2**63-1), 2**63-1, 2**63-1, self.mode(0)), 2**63-1)
        self.assertEqual(self.call('compare', self.money(-1), self.money(1)), -1)

    def test_record_and_enum_validation_at_each_operation(self):
        malformed = [self.money(True), self.money(2**63), self.money(1, -1), self.money(1, currency='bad'),
                     Record(self.vm.type_id_of('core.status.ConvertError'), ['x']),
                     Record(self.vm.type_id_of('core.money.Money'), [])]
        for value in malformed:
            self.error(self.call('format', value))
            self.error(self.call('rescale', value, 2, self.mode(0)))
            self.error(self.call('multiply_ratio', value, 1, 1, self.mode(0)))
            self.error(self.call('add', value, self.money(1)))
        for mode in [self.mode(-1), self.mode(6), self.money(0), EnumVal(self.vm.type_id_of('core.money.Rounding'), 0, [1])]:
            self.error(self.call('rescale', self.money(1), 2, mode))

    def test_signed_rounding_matches_decimal_oracle(self):
        rng = random.Random(17092026)
        modes = {1: ROUND_HALF_EVEN, 2: ROUND_HALF_UP, 3: ROUND_DOWN, 4: ROUND_FLOOR, 5: ROUND_CEILING}
        cases = [(u, n, d) for u in [-25, -15, -5, 0, 5, 15, 25] for n in [-1, 1] for d in [-10, 10]]
        cases += [(rng.randrange(-10**12, 10**12), rng.randrange(-1000, 1001), rng.choice([-1, 1])*rng.randrange(1, 1001)) for _ in range(120)]
        with localcontext() as ctx:
            ctx.prec = 100
            for mode, rounding in modes.items():
                for units, n, d in cases:
                    expected = int((Decimal(units) * Decimal(n) / Decimal(d)).to_integral_value(rounding=rounding))
                    self.units(self.call('multiply_ratio', self.money(units), n, d, self.mode(mode)), expected)
            for units in [-1255, -1245, -1, 0, 1, 1245, 1255]:
                for mode, rounding in modes.items():
                    expected = int((Decimal(units)/100).to_integral_value(rounding=rounding))
                    self.units(self.call('rescale', self.money(units, 4), 2, self.mode(mode)), expected)

    def test_exact_mode_and_add_subtract_oracle(self):
        with localcontext() as ctx:
            ctx.prec = 100
            ctx.traps[Inexact] = True
            for units in range(-25, 26):
                try:
                    expected = int((Decimal(units)/10).quantize(Decimal(1)))
                except Inexact:
                    self.error(self.call('multiply_ratio', self.money(units), 1, 10, self.mode(0)))
                else:
                    self.units(self.call('multiply_ratio', self.money(units), 1, 10, self.mode(0)), expected)
                self.units(self.call('add', self.money(units), self.money(17)), int(Decimal(units)+17))
                self.units(self.call('subtract', self.money(units), self.money(17)), int(Decimal(units)-17))

    def test_decimal_text_rounding_oracle(self):
        modes = {1: ROUND_HALF_EVEN, 2: ROUND_HALF_UP, 3: ROUND_DOWN, 4: ROUND_FLOOR, 5: ROUND_CEILING}
        with localcontext() as context:
            context.prec = 100
            for text in ['-0.000000000000000000000000000000000001', '-1.255', '-1.245', '1.255', '1.245', '0.005', '2.5499999999999998', '0.55000000000000004']:
                for scale in [0, 2, 6, 18]:
                    for mode, rounding in modes.items():
                        expected = int((Decimal(text)*10**scale).to_integral_value(rounding=rounding))
                        self.units(self.call('parse', text, scale, 'GBP', self.mode(mode)), expected, scale)
