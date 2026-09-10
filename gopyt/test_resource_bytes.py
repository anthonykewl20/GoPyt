import unittest
from unittest.mock import patch
from gopyt.resource_budget import ResourceBudget, ResourceLimits, ResourceLimitError
from gopyt.resource_bytes import ByteBuilder
from gopyt.resource_control import ResourceClosedError


class ByteOwnership(unittest.TestCase):
    def test_cancelled_json_releases_admitted_chunks(self):
        from gopyt import gobyte, jsonc
        from gopyt.vm import Cancelled
        art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_STR)])
        budget = self.budget(100000)
        def check():
            if budget.snapshot()['used']['native_bytes']:
                raise Cancelled()
        try:
            jsonc.encode_owned_bytes(art, 'x' * 10000, 0, budget=budget,
                                     max_bytes=20000, check_context=check)
        except Cancelled as error:
            trace = error.__traceback__
            builders = []
            while trace is not None:
                output = trace.tb_frame.f_locals.get('output')
                if isinstance(output, jsonc._Encoder): builders.append(output.output)
                trace = trace.tb_next
            self.assertTrue(builders)
            self.assertTrue(all(builder.closed and not builder.entries for builder in builders))
        else:
            self.fail('cancellation was not observed')
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_metadata_allocation_failure_releases_reservations(self):
        class RejectAppend(list):
            def append(self, item): raise MemoryError('chunk metadata unavailable')
        budget = self.budget(100)
        builder = ByteBuilder(budget)
        builder.entries = RejectAppend()
        with self.assertRaises(MemoryError): builder.append_text('hello')
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        builder.close()
        builder = ByteBuilder(budget)
        builder.append_text('hello')
        with patch('gopyt.resource_bytes.BytePayload', side_effect=MemoryError('owner unavailable')):
            with self.assertRaises(MemoryError): builder.finish()
        self.assertEqual(builder.entries, [])
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_owned_json_matches_independent_encoding(self):
        import json
        from gopyt import gobyte, jsonc
        art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_STR)])
        budget = self.budget(100000)
        value = 'é😀\n' * 100
        expected = json.dumps(value, ensure_ascii=False).encode()
        with jsonc.encode_owned_bytes(art, value, 0, budget=budget,
                                     max_bytes=len(expected)) as payload:
            self.assertEqual(payload.data, expected)
            self.assertEqual(budget.snapshot()['used']['native_bytes'], len(expected))
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        with self.assertRaises(jsonc.ConvertFail):
            jsonc.encode_owned_bytes(art, value, 0, budget=budget, max_bytes=len(expected)-1)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def budget(self, capacity):
        return ResourceBudget(ResourceLimits(capacity, 0, 0, 0))

    def test_payload_retains_charge_after_builder_finishes(self):
        budget = self.budget(20)
        builder = ByteBuilder(budget)
        builder.append_text('é')
        builder.append_text('😀')
        self.assertEqual(budget.snapshot()['used']['native_bytes'], 6)
        payload = builder.finish()
        self.assertEqual(payload.data, 'é😀'.encode())
        self.assertEqual(budget.snapshot()['used']['native_bytes'], 6)
        self.assertEqual(budget.snapshot()['peak']['native_bytes'], 12)
        with payload:
            pass
        self.assertEqual(payload.data, b'')
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        payload.close()
        with self.assertRaises(ResourceClosedError): builder.append_text('x')

    def test_final_copy_rejection_releases_chunks(self):
        budget = self.budget(9)
        builder = ByteBuilder(budget)
        for _ in range(6): builder.append_text('a')
        with self.assertRaises(ResourceLimitError): builder.finish()
        self.assertEqual(builder.entries, [])
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_encoding_failure_and_preallocation_rejection(self):
        budget = self.budget(4)
        builder = ByteBuilder(budget)
        with self.assertRaises(UnicodeEncodeError): builder.append_text('\ud800')
        self.assertEqual(builder.entries, [])
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        with self.assertRaises(ResourceLimitError): builder.append_text('ab')
        builder.append_text('a')
        with builder.finish() as payload:
            self.assertEqual(payload.data, b'a')
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
