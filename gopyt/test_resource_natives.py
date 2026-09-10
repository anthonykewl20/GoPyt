"""Unpublished native handles remain owned when physical cleanup needs retry."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from gopyt.heap import Heap
from gopyt.resource_budget import ResourceBudget, ResourceLimits
from gopyt.resource_buffer import Buffer
from gopyt.resource_natives import install


class NativeResourceCleanup(unittest.TestCase):
    def test_failed_publication_and_close_keep_unpublished_owner(self):
        heap = Heap()
        budget = ResourceBudget(ResourceLimits(8, 0, 0, 1))
        vm = SimpleNamespace(heap=heap, resource_budget=budget,
                             check_cancelled=lambda: None,
                             type_id_of=lambda name: 1)
        calls = []

        def allocate(budget, size, *, context):
            owner = Buffer(budget, size)

            def close(payload):
                calls.append(len(payload))
                if len(calls) == 1:
                    raise OSError('injected physical cleanup failure')
                payload.clear()

            owner._control._closer = close
            return owner

        table = {}
        install(table)
        with patch('gopyt.resource_natives.Buffer', side_effect=allocate):
            with patch.object(heap, 'adopt', side_effect=RuntimeError('publication failed')):
                with self.assertRaisesRegex(RuntimeError, 'publication failed'):
                    table['data.buffer.allocate'](vm, [8], None)
        self.assertEqual(heap.objects, {})
        self.assertEqual(heap.pending_resources(), 1)
        self.assertEqual(budget.snapshot()['used']['native_bytes'], 8)
        self.assertEqual(calls, [8])
        self.assertEqual(heap.drain_resources(), 1)
        self.assertEqual(calls, [8, 8])
        self.assertEqual(heap.pending_resources(), 0)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
