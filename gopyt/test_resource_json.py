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
