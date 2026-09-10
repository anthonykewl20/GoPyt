import contextlib
import io
import tempfile
import unittest
from unittest.mock import patch

from gopyt.store_admin import main, MaintenanceContext


class MaintenanceBudget(unittest.TestCase):
    def test_zero_descriptor_budget_reports_failure_and_closes_registry(self):
        contexts = []
        def create(*args):
            context = MaintenanceContext(*args)
            contexts.append(context)
            return context
        with tempfile.TemporaryDirectory() as root:
            with patch('gopyt.store_admin.MaintenanceContext', new=create):
                with contextlib.redirect_stderr(io.StringIO()) as errors:
                    result = main(['restore', '--root', root, '--backup', root + '/missing',
                                   '--expected-generation', '0', '--reason', 'test',
                                   '--descriptors', '0'])
        self.assertEqual(result, 2)
        self.assertIn('storage maintenance failed', errors.getvalue())
        self.assertEqual(contexts[0].descriptors.pending(), 0)
        self.assertEqual(contexts[0].resource_budget.snapshot()['active_reservations'], 0)

    def test_interrupt_still_closes_acquired_descriptor(self):
        contexts = []
        class InterruptedStore:
            def __init__(self, root, *, context):
                contexts.append(context)
                context.descriptors.open(root, 0)
            def anchor_status(self):
                raise KeyboardInterrupt()
        with tempfile.TemporaryDirectory() as root:
            with patch('gopyt.store_admin.Store', new=InterruptedStore):
                with self.assertRaises(KeyboardInterrupt):
                    main(['status', '--root', root])
        self.assertEqual(contexts[0].descriptors.pending(), 0)
        self.assertEqual(contexts[0].resource_budget.snapshot()['active_reservations'], 0)

    def test_cancel_after_backup_read_releases_payload_and_descriptor(self):
        from gopyt.files import regular_file
        from gopyt.storage import Store
        class Cancelled(Exception):
            pass
        class Context(MaintenanceContext):
            consumed = False
            def check_cancelled(self):
                if self.consumed:
                    raise Cancelled()
        context = Context(200000, 32)
        self.addCleanup(context.close)
        observed = []
        @contextlib.contextmanager
        def reader(*args, **kwargs):
            with regular_file(*args, **kwargs) as stream:
                class Stream:
                    def readinto(self, buffer):
                        try:
                            count = stream.readinto(buffer)
                            observed.append(count)
                            context.consumed = True
                            return count
                        finally:
                            buffer = None
                yield Stream()
        failure = None
        with tempfile.NamedTemporaryFile() as backup:
            backup.write(b'backup'); backup.flush()
            with patch('gopyt.storage.regular_file', new=reader):
                try:
                    Store(context=context).restore_anchor_file(
                        backup.name, expected_generation=0, reason='test')
                except Cancelled as exc:
                    failure = exc
        self.assertIsNotNone(failure)
        self.assertEqual(observed, [6])
        self.assertEqual(context.descriptors.pending(), 0)
        self.assertEqual(context.resource_budget.snapshot()['active_reservations'], 0)
