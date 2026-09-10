"""Tracing edges and deferred native cleanup obey the mutator-lock boundary."""
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from gopyt.heap import Heap
from gopyt.resource_budget import ResourceBudget, ResourceLimits
from gopyt.resource_buffer import Buffer
from gopyt.resource_control import ResourceClosedError


class ResourceTracing(unittest.TestCase):
    def test_failed_drain_batch_allocation_does_not_disable_future_cleanup(self):
        heap, budget, owner = self.fixture()
        heap.adopt(owner)
        heap.collect()
        with patch('gopyt.heap.islice', side_effect=MemoryError('batch allocation failed')):
            with self.assertRaises(MemoryError):
                heap.drain_resources()
        self.assertEqual(heap.pending_resources(), 1)
        self.assertEqual(budget.snapshot()['used']['native_bytes'], 8)
        self.assertEqual(heap.drain_resources(), 1)
        self.assertEqual(heap.pending_resources(), 0)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def fixture(self):
        budget = ResourceBudget(ResourceLimits(32,0,0,8))
        heap = Heap()
        owner = Buffer(budget,8)
        owner.write(0,b'abcdefgh')
        return heap,budget,owner

    def test_pinned_nested_view_retains_owner_and_collects_closed_parent(self):
        heap,budget,owner=self.fixture()
        parent=owner.view(1,6);child=parent.view(2,3)
        heap.adopt([owner,parent,child])
        with heap.pin(child):
            heap.collect()
            self.assertEqual(child.read(0,3),b'def')
            self.assertEqual(heap.pending_resources(),1)
            heap.drain_resources()
            with self.assertRaises(ResourceClosedError):parent.read(0,1)
            self.assertEqual(child.read(0,3),b'def')
            self.assertEqual(budget.snapshot()['used']['handles'],2)
        heap.collect()
        heap.drain_resources()
        self.assertEqual(budget.snapshot()['active_reservations'],0)
        self.assertEqual(heap.pending_resources(),0)

    def test_instruction_cleanup_runs_without_heap_lock(self):
        heap,budget,owner=self.fixture()
        acquired=[]
        def closer(payload):
            def observer():
                held=heap.lock.acquire(timeout=.5)
                acquired.append(held)
                if held:heap.lock.release()
            thread=threading.Thread(target=observer)
            thread.start();thread.join(2)
            self.assertFalse(thread.is_alive())
            payload.clear()
        owner._control._closer=closer
        frame=SimpleNamespace(stack=[owner],locals=[])
        with heap.frame(frame):
            with heap.step(frame):
                frame.stack.clear()
                heap.collect()
                self.assertEqual(acquired,[])
                self.assertEqual(budget.snapshot()['used']['native_bytes'],8)
        self.assertEqual(acquired,[True])
        self.assertEqual(budget.snapshot()['active_reservations'],0)

    def test_lease_survives_collection_and_deferred_work_is_retained(self):
        heap,budget,owner=self.fixture();heap.adopt(owner)
        with owner._control.lease() as payload:
            heap.collect();self.assertEqual(heap.drain_resources(),0)
            self.assertEqual(heap.pending_resources(),1)
            self.assertEqual(bytes(payload),b'abcdefgh')
            self.assertEqual(budget.snapshot()['used']['native_bytes'],8)
        self.assertEqual(budget.snapshot()['used']['native_bytes'],0)
        self.assertEqual(heap.drain_resources(),1)
        self.assertEqual(heap.pending_resources(),0)

    def test_failed_release_and_drain_limit_keep_ownership(self):
        heap,budget,owner=self.fixture();other=Buffer(budget,8)
        calls=[]
        def closer(payload):
            calls.append(1)
            if len(calls)==1:raise OSError('injected')
            payload.clear()
        owner._control._closer=closer
        heap.adopt(owner);heap.adopt(other);heap.collect()
        self.assertEqual(heap.drain_resources(limit=1),0)
        self.assertEqual(heap.pending_resources(),2)
        self.assertEqual(budget.snapshot()['used']['native_bytes'],16)
        self.assertEqual(heap.drain_resources(limit=1),1)
        self.assertEqual(heap.pending_resources(),1)
        self.assertEqual(heap.drain_resources(limit=1),1)
        self.assertEqual(budget.snapshot()['active_reservations'],0)
