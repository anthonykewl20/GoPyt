"""Host-level checked ownership; compiled/GC qualification is still required."""
import concurrent.futures
import unittest

from gopyt.resource_budget import ResourceBudget, ResourceLimits, ResourceLimitError
from gopyt.resource_buffer import Buffer
from gopyt.resource_control import ResourceClosedError


class CheckedBuffers(unittest.TestCase):
    def test_nested_views_alias_and_close_rules(self):
        budget = ResourceBudget(ResourceLimits(32,0,0,4))
        owner = Buffer(budget, 8); owner.write(0, b'abcdefgh')
        parent = owner.view(2, 5); child = parent.view(1, 3)
        self.assertEqual(child.read(0,3), b'def')
        alias = parent; parent.close()
        with self.assertRaises(ResourceClosedError): alias.read(0,1)
        child.write(0,b'XYZ')
        self.assertEqual(owner.read(0,8), b'abcXYZgh')
        self.assertTrue(owner.close())
        with self.assertRaises(ResourceClosedError): child.read(0,1)
        self.assertEqual(budget.snapshot()['used']['native_bytes'], 0)
        child.close(); child.close()
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_freeze_revokes_every_mutable_alias(self):
        budget = ResourceBudget(ResourceLimits(32,0,0,4))
        owner = Buffer(budget, 4); owner.write(0,b'data')
        view = owner.view(0,4); owner.freeze()
        for handle in (owner,view):
            with self.assertRaises(ValueError): handle.write(0,b'X')
            self.assertEqual(handle.read(0,4),b'data')
        view.close();owner.close()
        self.assertEqual(budget.snapshot()['active_reservations'],0)

    def test_exhaustion_and_bad_ranges_leave_data_and_charges_unchanged(self):
        budget = ResourceBudget(ResourceLimits(4,0,0,1))
        owner = Buffer(budget,4)
        for operation in (lambda:owner.view(0,4),lambda:owner.write(0,b'X'),lambda:owner.read(0,1)):
            with self.assertRaises(ResourceLimitError):operation()
        for start,length in [(-1,1),(0,5),(True,1),(3,2)]:
            with self.assertRaises(ValueError):owner.read(start,length)
        self.assertEqual(budget.snapshot()['used'],dict(native_bytes=4,mapped_bytes=0,descriptors=0,handles=1))
        owner.close();self.assertEqual(budget.snapshot()['active_reservations'],0)

    def test_parallel_fixed_size_updates_are_serialized(self):
        # Owner plus temporary copy plus retained immutable read result.
        budget = ResourceBudget(ResourceLimits(192,0,0,16))
        owner = Buffer(budget,64)
        views = [owner.view(i*8,8) for i in range(8)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda item:item[1].write(0,bytes([item[0]])*8),enumerate(views)))
        self.assertEqual(owner.read(0,64),b''.join(bytes([i])*8 for i in range(8)))
        for view in views:view.close()
        owner.close();self.assertEqual(budget.snapshot()['active_reservations'],0)

    def test_cancelled_wait_releases_lease_without_mutation(self):
        import threading
        class Cancelled(Exception):
            pass
        class Context:
            def __init__(self):
                self.cancel = threading.Event()
                self.entered = threading.Event()
            def check_cancelled(self):
                self.entered.set()
                if self.cancel.is_set():
                    raise Cancelled()
        context=Context()
        budget=ResourceBudget(ResourceLimits(32,0,0,4))
        owner=Buffer(budget,4,context=context)
        owner.write(0,b'data');view=owner.view(0,4)
        for handle in (owner,view):
            context.entered.clear();context.cancel.clear()
            owner._operation_lock.acquire()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(handle.write,0,b'FAIL')
                try:
                    self.assertTrue(context.entered.wait(2))
                    context.cancel.set()
                    with self.assertRaises(Cancelled):future.result(2)
                    self.assertEqual(owner._control.snapshot()['leases'],0)
                finally:
                    owner._operation_lock.release()
            context.cancel.clear()
            self.assertEqual(owner.read(0,4),b'data')
        view.close();owner.close()
        self.assertEqual(budget.snapshot()['active_reservations'],0)

    def test_cancelled_construction_returns_reservation(self):
        class Cancelled(Exception):
            pass
        class Context:
            calls=0
            def check_cancelled(self):
                self.calls+=1
                if self.calls==2:raise Cancelled()
        context=Context()
        budget=ResourceBudget(ResourceLimits(32,0,0,4))
        with self.assertRaises(Cancelled):Buffer(budget,16,context=context)
        self.assertEqual(budget.snapshot()['active_reservations'],0)
        self.assertEqual(budget.snapshot()['used']['native_bytes'],0)


class BufferReadOwnership(unittest.TestCase):
    def test_read_survives_owner_and_view_close(self):
        for use_view in (False, True):
            budget = ResourceBudget(ResourceLimits(12, 0, 0, 2))
            owner = Buffer(budget, 4)
            owner.write(0, b'abcd')
            view = owner.view(0, 4)
            result = (view if use_view else owner).read(0, 4)
            view.close()
            owner.close()
            self.assertEqual(result, b'abcd')
            self.assertEqual(budget.snapshot()['used']['native_bytes'], 4)
            del result
            self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_read_copy_overlap_rejection_preserves_owner(self):
        budget = ResourceBudget(ResourceLimits(11, 0, 0, 1))
        owner = Buffer(budget, 4)
        owner.write(0, b'abcd')
        with self.assertRaises(ResourceLimitError):
            owner.read(0, 4)
        self.assertEqual(budget.snapshot()['used']['native_bytes'], 4)
        owner.close()
        self.assertEqual(budget.snapshot()['active_reservations'], 0)


class BufferOwnedByteInputs(unittest.TestCase):
    def test_read_result_can_be_written_without_losing_source_charge(self):
        budget = ResourceBudget(ResourceLimits(100, 0, 0, 4))
        source = Buffer(budget, 4)
        target = Buffer(budget, 4)
        source.write(0, b'abcd')
        data = source.read(0, 4)
        source.close()
        target.write(0, data)
        view = target.view(0, 4)
        view.write(0, data)
        self.assertEqual(target.read(0, 4), b'abcd')
        view.close()
        target.close()
        self.assertEqual(budget.snapshot()['used']['native_bytes'], 4)
        del data
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    @unittest.skipUnless(__import__('sys').platform == 'linux', 'sealed mappings require Linux')
    def test_read_result_can_be_mapped_and_retains_input_ownership(self):
        budget = ResourceBudget(ResourceLimits(100, 100, 4, 4))
        source = Buffer(budget, 4)
        source.write(0, b'abcd')
        data = source.read(0, 4)
        source.close()
        mapped = Buffer.map_bytes(budget, data)
        self.assertEqual(mapped.read(0, 4), b'abcd')
        mapped.close()
        self.assertEqual(budget.snapshot()['used']['native_bytes'], 4)
        del data
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
