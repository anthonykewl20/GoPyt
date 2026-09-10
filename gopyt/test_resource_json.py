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


class JsonArrayOwnership(unittest.TestCase):
    def test_growth_storage_and_alias_lifetime(self):
        from gopyt.resource_json import _JsonArray
        budget = ResourceBudget(ResourceLimits(100000, 0, 0, 0))
        array = _JsonArray(budget)
        baseline = list.__sizeof__(array)
        for index in range(1000):
            array.append_owned(index)
            self.assertEqual(budget.snapshot()['used']['native_bytes'],
                             list.__sizeof__(array) - baseline)
        self.assertEqual(array, list(range(1000)))
        alias = array
        del array
        self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
        del alias
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_resize_rejection_preserves_previous_array(self):
        import struct
        from gopyt.resource_json import _JsonArray
        budget = ResourceBudget(ResourceLimits(8 * struct.calcsize('P'), 0, 0, 0))
        array = _JsonArray(budget)
        for index in range(4):
            array.append_owned(index)
        with self.assertRaises(ResourceLimitError):
            array.append_owned(4)
        self.assertEqual(array, [0, 1, 2, 3])
        self.assertEqual(budget.snapshot()['used']['native_bytes'], 4 * struct.calcsize('P'))
        del array
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_cancelled_append_retained_traceback_clears_owner(self):
        from gopyt.resource_json import _JsonArray
        budget = ResourceBudget(ResourceLimits(10000, 0, 0, 0))
        array = _JsonArray(budget)
        class Cancelled(Exception):
            pass
        def check():
            if budget.snapshot()['used']['native_bytes']:
                raise Cancelled()
        failure = None
        try:
            array.append_owned('value', check)
        except Cancelled as error:
            failure = error
        self.assertIsNotNone(failure)
        self.assertEqual(array, ['value'])
        del array
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        tb = failure.__traceback__
        while tb:
            if tb.tb_frame.f_code.co_name == 'append_owned':
                self.assertIsNone(tb.tb_frame.f_locals['self'])
                self.assertIsNone(tb.tb_frame.f_locals['value'])
            tb = tb.tb_next


class JsonObjectOwnership(unittest.TestCase):
    def test_growth_and_string_subclass_keys(self):
        from gopyt.resource_json import _JsonObject
        class Key(str):
            pass
        budget = ResourceBudget(ResourceLimits(1000000, 0, 0, 0))
        result = _JsonObject(budget)
        baseline = dict.__sizeof__(result)
        for index in range(1000):
            key = str(index) if index % 2 else Key(str(index))
            result.insert_owned(key, index)
            self.assertLessEqual(dict.__sizeof__(result) - baseline,
                                 budget.snapshot()['used']['native_bytes'])
        self.assertEqual(result, {str(i): i for i in range(1000)})
        alias = result
        del result
        self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
        del alias
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_duplicate_and_capacity_rejection_preserve_object(self):
        import struct
        from gopyt.resource_json import _JsonObject
        budget = ResourceBudget(ResourceLimits(32 * struct.calcsize('P'), 0, 0, 0))
        result = _JsonObject(budget)
        result.insert_owned('a', 1)
        with self.assertRaises(ConvertFail):
            result.insert_owned('a', 2)
        with self.assertRaises(ResourceLimitError):
            result.insert_owned('b', 2)
        self.assertEqual(result, {'a': 1})
        del result
        self.assertEqual(budget.snapshot()['active_reservations'], 0)


class JsonOwnedParser(unittest.TestCase):
    def test_nested_values_match_oracle(self):
        from decimal import Decimal
        from gopyt.resource_json import parse_owned
        for source in ('null', 'true', 'false', '0', '-1.25', '1e1000000', '"é中"',
                       '[]', '{}', '[1,"x",null]', '{"a":[1,{"b":false}],"c":{}}',
                       ' \t\r\n {"x":1} \n'):
            budget = ResourceBudget(ResourceLimits(100000, 0, 0, 0))
            result = parse_owned(source, budget)
            self.assertEqual(result, json.loads(source, parse_float=Decimal))
            del result
            self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_malformed_partial_graph_releases_with_error_retained(self):
        from gopyt.resource_json import parse_owned
        for source in ('', '[', '{', '[1,]', '{"x":1,}', '{"x" 1}',
                       '{"x":1,"x":2}', '[1 2]', '01', 'true false',
                       '["allocated",{"key":[1,2,]}]', '"\\ud800"'):
            budget = ResourceBudget(ResourceLimits(100000, 0, 0, 0))
            failure = None
            try:
                parse_owned(source, budget)
            except ConvertFail as error:
                failure = error
            self.assertIsNotNone(failure, source)
            self.assertEqual(budget.snapshot()['active_reservations'], 0, source)


class JsonParserFailureBoundaries(unittest.TestCase):
    source = '{"a":[1,"two",{"b":1.25}],"c":[true,null,"é"]}'

    def test_every_cancellation_boundary_releases_partial_graph(self):
        from gopyt.resource_json import parse_owned
        calls = 0
        def count():
            nonlocal calls
            calls += 1
        budget = ResourceBudget(ResourceLimits(100000, 0, 0, 0))
        result = parse_owned(self.source, budget, count)
        del result
        total = calls
        class Cancelled(Exception):
            pass
        for stop in range(1, total + 1):
            calls = 0
            def check():
                nonlocal calls
                calls += 1
                if calls == stop:
                    raise Cancelled()
            failure = None
            try:
                parse_owned(self.source, budget, check)
            except Cancelled as error:
                failure = error
            self.assertIsNotNone(failure, stop)
            self.assertEqual(budget.snapshot()['active_reservations'], 0, stop)

    def test_budget_rejection_releases_partial_graph(self):
        from gopyt.resource_json import parse_owned
        for limit in range(0, 2000, 17):
            budget = ResourceBudget(ResourceLimits(limit, 0, 0, 0))
            failure = result = None
            try:
                result = parse_owned(self.source, budget)
            except ResourceLimitError as error:
                failure = error
            del result
            self.assertEqual(budget.snapshot()['active_reservations'], 0, limit)

    def test_child_alias_outlives_parent(self):
        from gopyt.resource_json import parse_owned
        budget = ResourceBudget(ResourceLimits(100000, 0, 0, 0))
        result = parse_owned(self.source, budget)
        child = result['a']
        before = budget.snapshot()['used']['native_bytes']
        del result
        remaining = budget.snapshot()['used']['native_bytes']
        self.assertGreater(remaining, 0)
        self.assertLess(remaining, before)
        self.assertEqual(child[1], 'two')
        del child
        self.assertEqual(budget.snapshot()['active_reservations'], 0)


class JsonOwnedValueEquality(unittest.TestCase):
    def test_owned_graph_matches_plain_graph_with_integer_widths_preserved(self):
        from gopyt.resource_json import parse_owned
        from gopyt.values import I32, U32, U64
        from gopyt.vm import value_eq
        budget = ResourceBudget(ResourceLimits(100000, 0, 0, 0))
        result = parse_owned('{"x":[1,true,"s"]}', budget)
        self.assertTrue(value_eq(result, {'x': [1, True, 's']}))
        self.assertTrue(value_eq({'x': [1, True, 's']}, result))
        for other in (True, I32(1), U32(1), U64(1)):
            self.assertFalse(value_eq(result['x'][0], other))
        self.assertFalse(value_eq(result, {'y': [1, True, 's']}))
        del result
        self.assertEqual(budget.snapshot()['active_reservations'], 0)


class JsonTypedInteger(unittest.TestCase):
    def test_width_boundaries_and_ownership(self):
        from gopyt.jsonc import INT_RANGE, INT_VALUE
        from gopyt.resource_json import integer_value
        from gopyt.vm import scalar_type_id, value_eq
        for tag, (lo, hi) in INT_RANGE.items():
            budget = ResourceBudget(ResourceLimits(10000, 0, 0, 0))
            for value in (lo, 0, hi):
                result = integer_value(value, tag, budget)
                expected = INT_VALUE[tag](value)
                self.assertEqual(scalar_type_id(result), scalar_type_id(expected))
                self.assertTrue(value_eq(result, expected))
                alias = result
                del result
                self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
                del alias
                self.assertEqual(budget.snapshot()['active_reservations'], 0)
            for value in (lo - 1, hi + 1, True):
                with self.assertRaises(ConvertFail):
                    integer_value(value, tag, budget)
                self.assertEqual(budget.snapshot()['active_reservations'], 0)


class JsonTypedDecimal(unittest.TestCase):
    def test_boundaries_and_rejection_match_existing_decoder(self):
        from decimal import Decimal
        from gopyt.jsonc import _as_int, INT_RANGE, INT_VALUE
        from gopyt.resource_json import decimal_integer_value
        from gopyt.vm import value_eq
        sources = ('0.0', '-0.00', '1.5', '9223372036854775808.0',
                   '18446744073709551615.0', '18446744073709551616.0',
                   '-9223372036854775808.0', '1e1000000', '1e-1000000',
                   '1.' + '0' * 10000)
        for tag, (lo, hi) in INT_RANGE.items():
            for source in sources:
                value = Decimal(source)
                expected = reason = None
                try:
                    expected = _as_int(value)
                    if not lo <= expected <= hi:
                        raise ConvertFail('range')
                except ConvertFail as error:
                    reason = error.message
                budget = ResourceBudget(ResourceLimits(1000000, 0, 0, 0))
                if reason is not None:
                    with self.assertRaises(ConvertFail) as caught:
                        decimal_integer_value(value, tag, budget)
                    self.assertEqual(caught.exception.message, reason)
                else:
                    result = decimal_integer_value(value, tag, budget)
                    self.assertTrue(value_eq(result, INT_VALUE[tag](expected)))
                    del result
                self.assertEqual(budget.snapshot()['active_reservations'], 0)


class JsonTypedNumericFailures(unittest.TestCase):
    def test_cancellation_at_every_typed_conversion_check(self):
        from decimal import Decimal
        from gopyt.gobyte import TE_U64
        from gopyt.resource_json import integer_value, decimal_integer_value
        class Cancelled(Exception):
            pass
        for producer, value in ((integer_value, 18446744073709551615),
                                (decimal_integer_value, Decimal('18446744073709551615.0'))):
            budget = ResourceBudget(ResourceLimits(100000, 0, 0, 0))
            calls = 0
            def count():
                nonlocal calls
                calls += 1
            result = producer(value, TE_U64, budget, count)
            del result
            total = calls
            for stop in range(1, total + 1):
                calls = 0
                def check():
                    nonlocal calls
                    calls += 1
                    if calls == stop:
                        raise Cancelled()
                failure = None
                try:
                    producer(value, TE_U64, budget, check)
                except Cancelled as error:
                    failure = error
                self.assertIsNotNone(failure)
                self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_capacity_failure_at_each_temporary_stage(self):
        from decimal import Decimal
        from gopyt.gobyte import TE_U64
        from gopyt.resource_json import decimal_integer_value
        for limit in range(0, 600):
            budget = ResourceBudget(ResourceLimits(limit, 0, 0, 0))
            result = failure = None
            try:
                result = decimal_integer_value(Decimal('18446744073709551615.0'),
                                               TE_U64, budget)
            except ResourceLimitError as error:
                failure = error
            del result
            self.assertEqual(budget.snapshot()['active_reservations'], 0, limit)


class JsonTypedGraph(unittest.TestCase):
    def test_nested_destinations_match_existing_conversion(self):
        from types import SimpleNamespace as NS
        from gopyt import jsonc as j
        from gopyt.resource_json import parse_owned, decode_value
        from gopyt.vm import value_eq
        art = NS(texprs=[NS(tag=j.TE_I64), NS(tag=j.TE_STR),
                         NS(tag=j.TE_OPT, a=0), NS(tag=j.TE_LIST, a=2),
                         NS(tag=j.TE_MAP, a=1, b=3)])
        budget = ResourceBudget(ResourceLimits(100000, 0, 0, 0))
        parsed = parse_owned('{"a":[1,null,2.0],"b":[]}', budget)
        before = budget.snapshot()['used']['native_bytes']
        result = decode_value(art, parsed, 4, budget)
        self.assertTrue(value_eq(result, j._dec(art, parsed, 4)))
        self.assertGreater(budget.snapshot()['used']['native_bytes'], before)
        del parsed
        self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
        del result
        self.assertEqual(budget.snapshot()['active_reservations'], 0)


class JsonNominalGraph(unittest.TestCase):
    def artifact(self):
        from types import SimpleNamespace as NS
        from gopyt import jsonc as j
        names = ('R', 'n', 'label', 'E', 'Ok')
        return NS(texprs=[NS(tag=j.TE_I64), NS(tag=j.TE_STR),
                         NS(tag=j.TE_OPT, a=1), NS(tag=j.TE_NOM, a=0),
                         NS(tag=j.TE_NOM, a=1), NS(tag=j.TE_UNION, members=(3, 0))],
                  types=[NS(kind=1, name=0, fields=((1, 0), (2, 2))),
                         NS(kind=2, name=3, variants=((4, ((1, 0),)),))],
                  const_str=lambda index: names[index])

    def test_record_enum_union_and_field_errors(self):
        from gopyt import jsonc as j
        from gopyt.resource_json import parse_owned, decode_value
        from gopyt.vm import value_eq
        art = self.artifact()
        for source, tag in (('{"n":1}', 3), ('{"n":2,"label":"x"}', 3),
                            ('{"Ok":{"n":3}}', 4), ('{"R":{"n":4}}', 5),
                            ('{"i64":5}', 5), ('{}', 3), ('{"n":1,"extra":2}', 3),
                            ('{"Ok":{}}', 4), ('{"Bad":{}}', 4)):
            budget = ResourceBudget(ResourceLimits(100000, 0, 0, 0))
            parsed = parse_owned(source, budget)
            expected = reason = None
            try:
                expected = j._dec(art, parsed, tag)
            except ConvertFail as error:
                reason = error.message
            if reason is None:
                result = decode_value(art, parsed, tag, budget)
                self.assertTrue(value_eq(result, expected))
                if tag == 5:
                    self.assertEqual(j._matches(art, result, 0), source == '{"i64":5}')
                del result
            else:
                with self.assertRaises(ConvertFail) as caught:
                    decode_value(art, parsed, tag, budget)
                self.assertEqual(caught.exception.message, reason)
            expected = parsed = None
            self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_cancelled_nominal_conversion_keeps_only_borrowed_graph(self):
        from gopyt.resource_json import parse_owned, decode_value
        art = self.artifact()
        budget = ResourceBudget(ResourceLimits(100000, 0, 0, 0))
        parsed = parse_owned('{"n":2,"label":"x"}', budget)
        baseline = budget.snapshot()['used']['native_bytes']
        calls = 0
        def count():
            nonlocal calls
            calls += 1
        result = decode_value(art, parsed, 3, budget, count)
        del result
        total = calls
        class Cancelled(Exception):
            pass
        for stop in range(1, total + 1):
            calls = 0
            def check():
                nonlocal calls
                calls += 1
                if calls == stop:
                    raise Cancelled()
            failure = None
            try:
                decode_value(art, parsed, 3, budget, check)
            except Cancelled as error:
                failure = error
            self.assertIsNotNone(failure)
            self.assertEqual(budget.snapshot()['used']['native_bytes'], baseline)
        del parsed
        self.assertEqual(budget.snapshot()['active_reservations'], 0)


class JsonNativeRouting(unittest.TestCase):
    def test_decode_native_uses_vm_budget_and_owned_result(self):
        from types import SimpleNamespace as NS
        from gopyt import gobyte, natives, ops
        from gopyt.vm import Trap
        art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_I64),
                                     gobyte.TExpr(gobyte.TE_LIST, a=0)])
        budget = ResourceBudget(ResourceLimits(10000, 0, 0, 0))
        vm = NS(art=art, resource_budget=budget, check_cancelled=lambda: None)
        result = natives.NATIVES['data.json.decode'](vm, ['[1,2.0]'], NS(ret=1))
        self.assertEqual(result, [1, 2])
        self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
        del result
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        vm.resource_budget = ResourceBudget(ResourceLimits(0, 0, 0, 0))
        with self.assertRaises(Trap) as caught:
            natives.NATIVES['data.json.decode'](vm, ['[1]'], NS(ret=1))
        self.assertEqual(caught.exception.code, ops.TRAP_ALLOC)
        self.assertEqual(vm.resource_budget.snapshot()['active_reservations'], 0)


class JsonModelResponse(unittest.TestCase):
    def test_text_alias_and_invalid_response_cleanup(self):
        from types import SimpleNamespace as NS
        from gopyt.natives import _model_response_text
        budget = ResourceBudget(ResourceLimits(10000, 0, 0, 0))
        vm = NS(resource_budget=budget, type_id_of=lambda name: 42)
        result = _model_response_text(vm, '{"text":"answer"}', lambda: None)
        self.assertEqual(result, 'answer')
        self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
        del result
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        for source in ('{"text":1}', '{"text":"x","extra":1}', '{"text":', '[]'):
            result = _model_response_text(vm, source, lambda: None)
            self.assertEqual(result.fields, ['decode'])
            self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_budget_exhaustion_is_allocation_trap(self):
        from types import SimpleNamespace as NS
        from gopyt.natives import _model_response_text
        from gopyt.vm import Trap
        from gopyt import ops
        budget = ResourceBudget(ResourceLimits(0, 0, 0, 0))
        with self.assertRaises(Trap) as caught:
            _model_response_text(NS(resource_budget=budget), '{"text":"x"}', lambda: None)
        self.assertEqual(caught.exception.code, ops.TRAP_ALLOC)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)


class JsonOwnedScalarConsumers(unittest.TestCase):
    def test_arithmetic_money_and_time_accept_owned_i64_only(self):
        from gopyt import money, temporal, ops, gobyte
        from gopyt.resource_json import integer_value
        from gopyt.vm import _arith, Trap
        budget = ResourceBudget(ResourceLimits(10000, 0, 0, 0))
        result = integer_value(7, gobyte.TE_I64, budget)
        self.assertEqual(_arith(ops.ADD_I64, result, 2), 9)
        self.assertEqual(money.integer(result), 7)
        self.assertEqual(temporal.integer(result), 7)
        del result
        for tag in (gobyte.TE_I32, gobyte.TE_U32, gobyte.TE_U64):
            result = integer_value(7, tag, budget)
            with self.assertRaises(Trap):
                _arith(ops.ADD_I64, result, 2)
            with self.assertRaises(money.MoneyError):
                money.integer(result)
            with self.assertRaises(temporal.TimeError):
                temporal.integer(result)
            del result
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_owned_currency_and_time_text(self):
        from gopyt import money, temporal
        budget = ResourceBudget(ResourceLimits(10000, 0, 0, 0))
        currency, _ = string_token('"USD"', 0, budget)
        self.assertEqual(money.currency_value(currency), 'USD')
        del currency
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
