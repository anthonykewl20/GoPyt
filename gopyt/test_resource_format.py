"""Owned integer formatting and canonical JSON output lifetime regressions."""
import json
import unittest
from gopyt import gobyte, jsonc
from gopyt.resource_budget import ResourceBudget, ResourceLimits, ResourceLimitError
from gopyt.resource_text import format_integer


class FormatAdmission(unittest.TestCase):
    def budget(self, size=1048576):
        return ResourceBudget(ResourceLimits(size, 0, 0, 0))

    def test_integer_boundaries_and_surviving_alias(self):
        for value in (-(1 << 63), -1, 0, 1, (1 << 63)-1, (1 << 64)-1):
            budget = self.budget()
            result = format_integer(value, budget)
            self.assertEqual(result, str(value))
            alias = result
            del result
            self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
            del alias
            self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_json_owned_output_matches_independent_encoder(self):
        art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_STR)])
        for text in ('', 'plain', '\x00\n\t"\\', 'é中😀', 'a'*4095+'\n😀'+'b'*4100):
            budget = self.budget()
            result = jsonc.encode(art, text, 0, budget=budget)
            self.assertEqual(result, json.dumps(text, ensure_ascii=False))
            self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
            del result
            self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_retained_failures_release_formatting_intermediates(self):
        art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_STR)])
        producers = (lambda b, c: format_integer(-(1 << 63), b, c),
                     lambda b, c: jsonc.encode(art, '\x00é😀'*8, 0,
                                              budget=b, check_context=c))
        class Stop(Exception):
            pass
        for producer in producers:
            for capacity in range(0, 2400, 17):
                budget = self.budget(capacity)
                failure = None
                try:
                    result = producer(budget, lambda: None)
                except ResourceLimitError as error:
                    failure = error
                else:
                    del result
                self.assertEqual(budget.snapshot()['active_reservations'], 0,
                                 (capacity, failure))
            checks = []
            result = producer(self.budget(), lambda: checks.append(1))
            del result
            for target in range(1, len(checks) + 1):
                count = 0
                budget = self.budget()
                def check():
                    nonlocal count
                    count += 1
                    if count == target:
                        raise Stop()
                failure = None
                try:
                    producer(budget, check)
                except Stop as error:
                    failure = error
                self.assertIsNotNone(failure)
                self.assertEqual(budget.snapshot()['active_reservations'], 0, target)

    def test_invalid_unicode_rejects_without_retained_temporary_owner(self):
        art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_STR)])
        budget = self.budget()
        failure = None
        try:
            jsonc.encode(art, '\ud800', 0, budget=budget)
        except jsonc.ConvertFail as error:
            failure = error
        self.assertIsNotNone(failure)
        self.assertEqual(failure.message, 'utf8')
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_owned_json_map_and_integer_tokens_match_oracle(self):
        art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_STR),
            gobyte.TExpr(gobyte.TE_U64), gobyte.TExpr(gobyte.TE_LIST, a=1),
            gobyte.TExpr(gobyte.TE_MAP, a=0, b=2)])
        value = {'😀': [(1 << 64)-1], 'a': [0, 1], 'é': []}
        budget = self.budget()
        result = jsonc.encode(art, value, 3, budget=budget)
        self.assertEqual(result, json.dumps(value, ensure_ascii=False,
                                           sort_keys=True, separators=(',', ':')))
        del result
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_compiled_format_json_and_trait_keep_output_charges(self):
        import tempfile
        from gopyt.cli import build
        from gopyt.testing import write_pkg
        from gopyt.vm import VM, Trap
        from gopyt import ops
        with tempfile.TemporaryDirectory() as root:
            write_pkg(root, {
                'spec/demo.gopyt': '''module demo
use core.status { ConvertError }
fn decimal(value: i64) -> str
fn render(value: str) -> str | ConvertError
fn via_trait(value: str) -> str | ConvertError
''',
                'impl/demo.gopyt': '''module demo
use core.status { ConvertError }
use core.str { from_i64 }
use data.json { encode }
use core.convert { Json }
fn decimal(value: i64) -> str
{
    return core.str.from_i64(value)
}
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
            _, art, ids = build(root)
            for name, value, expected in (('decimal', -(1 << 63), str(-(1 << 63))),
                                           ('render', 'é\n', '"é\\n"'),
                                           ('via_trait', 'é\n', '"é\\n"')):
                budget = self.budget()
                with VM(art, resource_budget=budget) as vm:
                    result = vm.call(ids['demo.'+name], [value])
                    self.assertEqual(result, expected)
                self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
                del result
                self.assertEqual(budget.snapshot()['active_reservations'], 0)
                budget = self.budget(0)
                with VM(art, resource_budget=budget) as vm:
                    with self.assertRaises(Trap) as caught:
                        vm.call(ids['demo.'+name], [value])
                    self.assertEqual(caught.exception.code, ops.TRAP_ALLOC)
                self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_retained_c_encoder_failure_releases_slice_capacity(self):
        from unittest.mock import patch
        art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_STR)])
        budget = self.budget()
        def fail(piece):
            try:
                raise MemoryError('injected C encoder failure')
            finally:
                piece = None
        failure = None
        with patch('_json.encode_basestring', new=fail):
            try:
                jsonc.encode(art, 'text'*2048, 0, budget=budget)
            except MemoryError as error:
                failure = error
        self.assertIsNotNone(failure)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
