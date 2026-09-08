"""Independent collector checks at instruction, native, and return boundaries.

These tests assert language-value survival and reclamation, without depending
on the collector's admission helpers or using elapsed time as a performance
assertion. Thread events force the interesting interleavings.
"""
import threading
from types import SimpleNamespace
import unittest

from gopyt.heap import Heap
from gopyt.values import EnumVal, Record, Some


def frame(stack=(), locals=()):
    return SimpleNamespace(stack=list(stack), locals=list(locals))


class HeapBoundaries(unittest.TestCase):
    def test_collection_traces_new_children_of_already_admitted_mutable_roots(self):
        heap = Heap()
        root = Record(0, [])
        with heap.pin(root):
            # Admission predates mutation: collecting must inspect the current
            # graph, including a cycle and map keys/values, rather than trusting
            # the old admission inventory.
            child = Some([EnumVal(1, 0, [{'key': b'value'}])])
            root.fields.extend([child, root])
            heap.collect()
            self.assertEqual(child.value[0].fields[0], {'key': b'value'})
            self.assertIs(root.fields[1], root)
        heap.collect()
        self.assertEqual(root.fields, [])
        self.assertIsNone(child.value)

    def test_step_admits_both_stack_and_local_graphs_before_threshold_collection(self):
        heap = Heap(threshold=1)
        stacked = Record(0, [Some(['stack'])])
        local = EnumVal(1, 0, [{'local': b'value'}])
        current = frame([stacked, 7, None], [local, False])
        with heap.frame(current):
            with heap.step(current):
                self.assertGreater(heap.collections, 0)
                self.assertEqual(stacked.fields[0].value, ['stack'])
                self.assertEqual(local.fields, [{'local': b'value'}])
        # Python host references above are explicitly not language roots.
        heap.collect()
        self.assertEqual(stacked.fields, [])
        self.assertEqual(local.fields, [])

    def test_handoff_survives_until_caller_stack_owns_the_return_value(self):
        heap = Heap(threshold=1)
        result = heap.handoff(Record(0, [Some(['returned'])]))
        heap.collect()
        self.assertEqual(result.fields[0].value, ['returned'])
        caller = frame([result])
        with heap.frame(caller):
            with heap.step(caller):
                heap.collect()
                self.assertEqual(result.fields[0].value, ['returned'])
            # The caller's next instruction consumed its handoff root.
            caller.stack.clear()
            heap.collect()
            self.assertEqual(result.fields, [])

    def test_one_threads_instruction_does_not_consume_another_threads_handoff(self):
        heap = Heap()
        ready, release, done = (threading.Event() for _ in range(3))
        shared, errors = [], []

        def return_on_other_thread():
            try:
                shared.append(heap.handoff(Record(0, ['other thread'])))
                ready.set()
                if not release.wait(5):
                    raise AssertionError('main thread did not release worker')
                heap.release_result()
            except BaseException as error:
                errors.append(error)
                ready.set()
            finally:
                done.set()

        worker = threading.Thread(target=return_on_other_thread, daemon=True)
        worker.start()
        try:
            self.assertTrue(ready.wait(5), 'worker did not publish handoff')
            self.assertFalse(errors)
            current = frame()
            with heap.frame(current), heap.step(current):
                heap.release_result()
                heap.collect()
                self.assertEqual(shared[0].fields, ['other thread'])
        finally:
            release.set()
            worker.join(5)
        self.assertTrue(done.is_set(), 'worker did not finish')
        self.assertFalse(errors)
        heap.collect()
        self.assertEqual(shared[0].fields, [])

    def test_released_native_boundary_allows_collection_with_arguments_pinned(self):
        heap = Heap()
        argument = Record(0, [Some(['argument'])])
        current = frame([argument])
        start, finished = threading.Event(), threading.Event()
        errors = []

        def collect_during_native():
            try:
                if not start.wait(5):
                    raise AssertionError('native boundary was not reached')
                heap.collect()
                if argument.fields[0].value != ['argument']:
                    raise AssertionError('pinned native argument was corrupted')
            except BaseException as error:
                errors.append(error)
            finally:
                finished.set()

        worker = threading.Thread(target=collect_during_native, daemon=True)
        worker.start()
        try:
            with heap.frame(current), heap.step(current):
                args = [current.stack.pop()]
                with heap.pin(args):
                    with heap.released():
                        start.set()
                        self.assertTrue(finished.wait(5), 'native boundary retained instruction lock')
                    self.assertFalse(errors)
                    self.assertEqual(argument.fields[0].value, ['argument'])
        finally:
            start.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        heap.collect()
        self.assertEqual(argument.fields, [])

    def test_exception_removes_frame_and_pin_roots_and_restores_released_lock(self):
        heap = Heap()
        value = Record(0, ['temporary'])
        current = frame([value])
        with self.assertRaisesRegex(RuntimeError, 'native failed'):
            with heap.frame(current), heap.step(current), heap.pin([value]):
                with heap.released():
                    raise RuntimeError('native failed')
        completed = threading.Event()

        def collect_from_other_thread():
            heap.collect()
            completed.set()

        worker = threading.Thread(target=collect_from_other_thread, daemon=True)
        worker.start()
        worker.join(5)
        self.assertTrue(completed.is_set(), 'instruction lock leaked on exceptional unwind')
        self.assertEqual(value.fields, [])


if __name__ == '__main__':
    unittest.main()
