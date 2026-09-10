import os
import tempfile
import unittest
from unittest.mock import patch

from gopyt.resource_budget import ResourceBudget, ResourceLimits, ResourceLimitError
from gopyt.storage import Store


class Context:
    def __init__(self, capacity):
        self.resource_budget = ResourceBudget(ResourceLimits(capacity, 0, 0, 0))

    def check_cancelled(self):
        pass


class SnapshotPayload(unittest.TestCase):
    def test_rejection_precedes_snapshot_consumption(self):
        context = Context(0)
        store = Store(context=context)
        with tempfile.NamedTemporaryFile() as stream:
            stream.write(b'snapshot')
            stream.seek(0)
            with patch.object(store, '_deserialize') as deserialize:
                with self.assertRaises(ResourceLimitError):
                    store._load(stream.fileno(), store._identity(os.fstat(stream.fileno())), None)
                deserialize.assert_not_called()
            self.assertEqual(stream.tell(), 0)
        self.assertEqual(context.resource_budget.snapshot()['active_reservations'], 0)

    def test_failed_decoder_retained_input_keeps_charge(self):
        context = Context(200000)
        store = Store(context=context)
        retained = []
        def fail(data, security):
            retained.append(data)
            raise ValueError('decoder failed')
        with tempfile.NamedTemporaryFile() as stream:
            stream.write(b'snapshot')
            stream.seek(0)
            with patch('gopyt.storage.unseal', new=fail):
                with self.assertRaises(ValueError):
                    store._load(stream.fileno(), store._identity(os.fstat(stream.fileno())), None)
        self.assertEqual(retained, [b'snapshot'])
        self.assertEqual(context.resource_budget.snapshot()['used']['native_bytes'], 8)
        retained.clear()
        self.assertEqual(context.resource_budget.snapshot()['active_reservations'], 0)
