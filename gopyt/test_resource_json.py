import json
import unittest
from gopyt.resource_budget import ResourceBudget, ResourceLimits, ResourceLimitError
from gopyt.resource_json import string_token
from gopyt.jsonc import ConvertFail


class JsonStringToken(unittest.TestCase):
    def test_tokens_match_json_oracle_and_retain_charge(self):
        for source in ('""', '"plain"', '"é中"', '"a\\n\\\"b"', '"\\ud83d\\ude00"'):
            budget = ResourceBudget(ResourceLimits(10000, 0, 0, 0))
            result, end = string_token(source + ' trailing', 0, budget)
            self.assertEqual(result, json.loads(source))
            self.assertEqual(end, len(source))
            self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
            del result
            self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_rejection_and_malformed_tokens_release(self):
        budget = ResourceBudget(ResourceLimits(0, 0, 0, 0))
        with self.assertRaises(ResourceLimitError):
            string_token('"text"', 0, budget)
        for source in ('"\\ud800"', '"\\x20"', '"\n"', '"unterminated'):
            budget = ResourceBudget(ResourceLimits(10000, 0, 0, 0))
            with self.assertRaises(ConvertFail):
                string_token(source, 0, budget)
            self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_error_after_long_prefix_releases_with_traceback_retained(self):
        source = (' ' * 100000) + '"\\x20"'
        budget = ResourceBudget(ResourceLimits(1000, 0, 0, 0))
        failure = None
        try:
            string_token(source, 100000, budget)
        except ConvertFail as error:
            failure = error
        self.assertIsNotNone(failure)
        self.assertIsNone(failure.__context__)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        self.assertLess(budget.snapshot()['peak']['native_bytes'], 1000)

    def test_escape_heavy_widening_matches_oracle(self):
        source = '"' + ('a\\n\\u00e9\\u4e2d\\ud83d\\ude00' * 100) + '"'
        budget = ResourceBudget(ResourceLimits(100000, 0, 0, 0))
        result, end = string_token(source, 0, budget)
        self.assertEqual(result, json.loads(source))
        self.assertEqual(end, len(source))
        del result
        self.assertEqual(budget.snapshot()['active_reservations'], 0)


class JsonNumberSpan(unittest.TestCase):
    def test_grammar_matches_standard_decoder_offsets(self):
        from decimal import Decimal
        from gopyt.resource_json import number_span
        decoder = json.JSONDecoder(parse_float=Decimal)
        for token in ('0', '-0', '123', '-12.50', '1e1000000', '1E-8', '0.25e+12'):
            source = token + ', trailing'
            expected, offset = decoder.raw_decode(source)
            end, decimal = number_span(source, 0)
            self.assertEqual(end, offset)
            self.assertEqual(decimal, isinstance(expected, Decimal))

    def test_invalid_digits_and_incomplete_numbers(self):
        from gopyt.resource_json import number_span
        for token in ('', '-', '+1', '.1', '1.', '1e', '1e+', '١', 'NaN', 'Infinity'):
            with self.subTest(token=token), self.assertRaises(ConvertFail):
                number_span(token, 0)
        # Leading zero is one valid token followed by invalid trailing input.
        self.assertEqual(number_span('01', 0), (1, False))

    def test_long_scan_cancellation_clears_input(self):
        from gopyt.resource_json import number_span
        class Cancelled(Exception):
            pass
        calls = 0
        def check():
            nonlocal calls
            calls += 1
            if calls == 3:
                raise Cancelled()
        failure = None
        try:
            number_span('1' * 20000, 0, check)
        except Cancelled as error:
            failure = error
        self.assertIsNotNone(failure)
        frame = failure.__traceback__
        while frame is not None:
            if frame.tb_frame.f_code.co_name == 'number_span':
                self.assertIsNone(frame.tb_frame.f_locals['text'])
            frame = frame.tb_next


class JsonIntegerToken(unittest.TestCase):
    def test_oracle_and_alias_ownership(self):
        from gopyt.resource_json import integer_token
        for token in ('0', '-0', '123456789', '-123456789012345678901234567890', '9' * 1000):
            budget = ResourceBudget(ResourceLimits(100000, 0, 0, 0))
            result, end = integer_token(token + ',', 0, budget)
            self.assertEqual(result, json.loads(token))
            self.assertEqual(hash(result), hash(json.loads(token)))
            self.assertEqual(end, len(token))
            alias = result
            del result
            self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
            del alias
            self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_capacity_and_digit_limit(self):
        import sys
        from gopyt.resource_json import integer_token
        budget = ResourceBudget(ResourceLimits(0, 0, 0, 0))
        with self.assertRaises(ResourceLimitError):
            integer_token('123', 0, budget)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        limit = sys.get_int_max_str_digits()
        if limit:
            with self.assertRaises(ConvertFail):
                integer_token('1' * (limit + 1), 0, budget)
            self.assertEqual(budget.snapshot()['peak']['native_bytes'], 0)

    def test_cancelled_conversion_clears_intermediates(self):
        from gopyt.resource_json import integer_token
        budget = ResourceBudget(ResourceLimits(100000, 0, 0, 0))
        class Cancelled(Exception):
            pass
        admitted_checks = 0
        def check():
            nonlocal admitted_checks
            if budget.snapshot()['used']['native_bytes']:
                admitted_checks += 1
                if admitted_checks == 3:
                    raise Cancelled()
        failure = None
        try:
            integer_token('7' * 100, 0, budget, check)
        except Cancelled as error:
            failure = error
        self.assertIsNotNone(failure)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        tb = failure.__traceback__
        while tb:
            if tb.tb_frame.f_code.co_name == 'integer_token':
                for name in ('text', 'raw', 'result', 'chunk'):
                    self.assertIsNone(tb.tb_frame.f_locals[name])
            tb = tb.tb_next

    def test_constructor_failure_releases_reserved_payload(self):
        from unittest.mock import patch
        from gopyt.resource_json import integer_token
        budget = ResourceBudget(ResourceLimits(10000, 0, 0, 0))
        def fail(value, reservation):
            try:
                raise MemoryError('injected integer allocation failure')
            finally:
                value = None
        failure = None
        with patch('gopyt.resource_json._ChargedInteger', new=fail):
            try:
                integer_token('12345678901234567890', 0, budget)
            except MemoryError as error:
                failure = error
        self.assertIsNotNone(failure)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        tb = failure.__traceback__
        while tb:
            if tb.tb_frame.f_code.co_name == 'integer_token':
                for name in ('text', 'raw', 'result', 'chunk'):
                    self.assertIsNone(tb.tb_frame.f_locals[name])
            tb = tb.tb_next


class JsonDecimalToken(unittest.TestCase):
    def test_oracle_and_alias_ownership(self):
        from decimal import Decimal
        from gopyt.resource_json import decimal_token
        for token in ('0.0', '-0.00', '1.25', '1e1000000', '1e-1000000', '1.234567890123456789e+200', '1.' + '2' * 1000):
            budget = ResourceBudget(ResourceLimits(100000, 0, 0, 0))
            result, end = decimal_token(token + ',', 0, budget)
            expected = json.loads(token, parse_float=Decimal)
            self.assertEqual(result.as_tuple(), expected.as_tuple())
            self.assertEqual(hash(result), hash(expected))
            self.assertEqual(end, len(token))
            alias = result
            del result
            self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
            del alias
            self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_admission_precedes_constructor(self):
        from unittest.mock import patch
        from gopyt.resource_json import decimal_token
        budget = ResourceBudget(ResourceLimits(0, 0, 0, 0))
        with patch('gopyt.resource_json._ChargedDecimal') as constructor:
            with self.assertRaises(ResourceLimitError):
                decimal_token('1.25', 0, budget)
            constructor.assert_not_called()
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_invalid_exponent_and_cancelled_result_cleanup(self):
        from gopyt.resource_json import decimal_token
        budget = ResourceBudget(ResourceLimits(10000, 0, 0, 0))
        failure = None
        try:
            decimal_token('1e999999999999999999999999999999', 0, budget)
        except ConvertFail as error:
            failure = error
        self.assertIsNotNone(failure)
        self.assertIsNone(failure.__context__)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        class Cancelled(Exception):
            pass
        def check():
            if budget.snapshot()['used']['native_bytes']:
                raise Cancelled()
        try:
            decimal_token('1.25', 0, budget, check)
        except Cancelled as error:
            failure = error
        self.assertIsInstance(failure, Cancelled)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        tb = failure.__traceback__
        while tb:
            if tb.tb_frame.f_code.co_name == 'decimal_token':
                for name in ('text', 'token', 'result'):
                    self.assertIsNone(tb.tb_frame.f_locals[name])
            tb = tb.tb_next

    def test_decimal_context_traps_match_oracle(self):
        from decimal import Decimal, InvalidOperation, localcontext
        from gopyt.resource_json import decimal_token
        token = '1e999999999999999999999999999999'
        budget = ResourceBudget(ResourceLimits(10000, 0, 0, 0))
        with localcontext() as context:
            context.traps[InvalidOperation] = False
            expected = Decimal(token)
            context.clear_flags()
            result, end = decimal_token(token, 0, budget)
            self.assertEqual(result.as_tuple(), expected.as_tuple())
            self.assertTrue(context.flags[InvalidOperation])
            self.assertEqual(end, len(token))
        del result
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
