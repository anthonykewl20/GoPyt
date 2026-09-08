"""Reference traces and lock invariants for the reusable instruction guard."""
import random
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from gopyt.heap import Heap
from gopyt import test_heap_reference
from gopyt.values import Record, Some


def trace(heap_type,seed):
    rng=random.Random(seed);heap=heap_type(threshold=4)
    frame=SimpleNamespace(stack=[],locals=[None]*3);host=[];history=[]
    def shape(value,seen=None):
        seen=set() if seen is None else seen
        if isinstance(value,(int,bool,str,bytes,type(None))):return value
        if id(value) in seen:return 'cycle'
        seen.add(id(value))
        if isinstance(value,Record):return ('record',tuple(shape(v,seen) for v in value.fields))
        if isinstance(value,Some):return ('some',shape(value.value,seen))
        if isinstance(value,list):return ('list',tuple(shape(v,seen) for v in value))
        return ('map',tuple((k,shape(v,seen)) for k,v in sorted(value.items())))
    with heap.frame(frame):
        for step in range(128):
            with heap.step(frame):
                action=rng.randrange(6)
                if action==0:
                    leaf=[step];node=Record(0,[Some(leaf)]);host.extend([leaf,node.fields[0],node]);frame.stack.append(node)
                elif action==1 and frame.stack:frame.locals[rng.randrange(3)]=frame.stack.pop()
                elif action==2:frame.locals[rng.randrange(3)]=None
                elif action==3 and frame.stack:frame.stack.pop()
                elif action==4:
                    cycle=[];cycle.append(cycle);host.append(cycle);frame.stack.append(cycle)
                else:heap.threshold=1
                heap.collect()
            history.append((heap.collections,heap.reclaimed,len(heap.objects),shape(frame.stack),shape(frame.locals),tuple(shape(v) for v in host)))
    heap.collect();history.append(tuple(shape(v) for v in host))
    return history


class HeapRefinement(unittest.TestCase):
    def test_seeded_graph_traces_match_retained_heap(self):
        for seed in range(16):
            with self.subTest(seed=seed):self.assertEqual(trace(Heap,seed),trace(test_heap_reference.Heap,seed))

    def test_guard_reuse_rescans_mutated_roots(self):
        heap=Heap(threshold=1);frame=SimpleNamespace(stack=[],locals=[])
        instruction=heap.step(frame)
        with heap.frame(frame):
            with instruction:frame.stack.append(Record(0,[Some([3])]))
            with instruction:
                heap.collect();self.assertEqual(frame.stack[0].fields[0].value,[3])
            frame.stack.clear()
            with instruction:heap.collect()
        self.assertEqual(len(heap.objects),0)

    def test_failed_enter_releases_lock_for_other_thread(self):
        heap=Heap(threshold=1);frame=SimpleNamespace(stack=[[]],locals=[])
        with patch.object(heap,'collect',side_effect=RuntimeError('injected')):
            with self.assertRaises(RuntimeError):
                with heap.step(frame):pass
        got=[]
        def acquire():
            owned=heap.lock.acquire(timeout=1);got.append(owned)
            if owned:heap.lock.release()
        thread=threading.Thread(target=acquire);thread.start();thread.join(2)
        self.assertEqual(got,[True])

    def test_failed_body_releases_reusable_guard(self):
        heap=Heap();frame=SimpleNamespace(stack=[],locals=[]);instruction=heap.step(frame)
        with self.assertRaises(ValueError):
            with instruction:raise ValueError('injected')
        with instruction:frame.stack.append(8)
        self.assertEqual(frame.stack,[8])
