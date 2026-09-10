"""Bounded handoff preserves lock ownership and cleanup on every exit path."""
from types import SimpleNamespace
import threading
import unittest

from gopyt.heap import Heap
from gopyt.resource_budget import ResourceBudget, ResourceLimits
from gopyt.resource_buffer import Buffer


class HeapBatches(unittest.TestCase):
    def other_thread_can_acquire(self, heap):
        result = []
        def run():
            acquired = heap.lock.acquire(timeout=.1)
            result.append(acquired)
            if acquired: heap.lock.release()
        thread = threading.Thread(target=run)
        thread.start()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        return result == [True]

    def test_native_release_and_batch_boundary_have_one_physical_lock(self):
        heap = Heap()
        guard = heap.step(SimpleNamespace(stack=[], locals=[]), quantum=2)
        try:
            with guard:
                self.assertFalse(self.other_thread_can_acquire(heap))
                with heap.released():
                    self.assertTrue(self.other_thread_can_acquire(heap))
                self.assertFalse(self.other_thread_can_acquire(heap))
            self.assertFalse(self.other_thread_can_acquire(heap))
            with guard:
                pass
            self.assertTrue(self.other_thread_can_acquire(heap))
        finally:
            guard.finish()

    def test_exception_and_early_frame_finish_release_immediately(self):
        heap = Heap()
        frame = SimpleNamespace(stack=[], locals=[])
        guard = heap.step(frame, quantum=32)
        with self.assertRaisesRegex(ValueError, 'injected'):
            with guard:
                raise ValueError('injected')
        self.assertTrue(self.other_thread_can_acquire(heap))
        with guard:
            pass
        guard.finish()
        guard.finish()
        self.assertTrue(self.other_thread_can_acquire(heap))

    def test_collection_retains_charge_until_release_and_closes_outside_lock(self):
        budget = ResourceBudget(ResourceLimits(8, 0, 0, 1))
        owner = Buffer(budget, 8)
        heap = Heap()
        frame = SimpleNamespace(stack=[owner], locals=[])
        guard = heap.step(frame, quantum=2)
        observations = []
        def close(payload):
            observations.append(self.other_thread_can_acquire(heap))
            payload.clear()
        owner._control._closer = close
        with heap.frame(frame):
            try:
                with guard:
                    frame.stack.clear()
                    heap.collect()
                self.assertEqual(observations, [])
                self.assertEqual(budget.snapshot()['used']['native_bytes'], 8)
                with guard:
                    pass
                self.assertEqual(observations, [True])
                self.assertEqual(budget.snapshot()['active_reservations'], 0)
            finally:
                guard.finish()
