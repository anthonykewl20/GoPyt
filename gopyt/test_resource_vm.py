"""Compiled resource natives, including nominal values and typed failure paths."""
import tempfile
import unittest
from gopyt.cli import build
from gopyt.testing import write_pkg
from gopyt.test_vm import module
from gopyt.vm import VM, Trap
from gopyt.values import Record, UNIT
from gopyt.resource_budget import ResourceBudget, ResourceLimits


class CompiledResources(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        operations=[('allocate','size: i64','Buffer','size'),
            ('map_bytes','payload: bytes','Buffer','payload'),
            ('read','buffer: Buffer, start: i64, length: i64','bytes','buffer, start, length'),
            ('write','buffer: Buffer, start: i64, payload: bytes','unit','buffer, start, payload'),
            ('freeze','buffer: Buffer','unit','buffer'),
            ('view','buffer: Buffer, start: i64, length: i64','View','buffer, start, length'),
            ('subview','view: View, start: i64, length: i64','View','view, start, length'),
            ('read_view','view: View, start: i64, length: i64','bytes','view, start, length'),
            ('write_view','view: View, start: i64, payload: bytes','unit','view, start, payload'),
            ('close','buffer: Buffer','unit','buffer'),('close_view','view: View','unit','view')]
        signatures=[];bodies=[]
        for name,args,result,call in operations:
            signature=f'task {name}({args}) -> {result} | ResourceError\n    effects {{ resource }}'
            signatures.append(signature)
            bodies.append(signature+'\n{\n    return data.buffer.'+name+'('+call+')\n}')
        files=module('\n\n'.join(signatures),'\n\n'.join(bodies),
            uses='use core.status { ResourceError }\nuse data.buffer { Buffer, View, '+', '.join(x[0] for x in operations)+' }',
            spec_uses='use core.status { ResourceError }\nuse data.buffer { Buffer, View }')
        write_pkg(temp.name,files,fmt=True)
        _,self.art,self.ids=build(temp.name)
        self.budget=ResourceBudget(ResourceLimits(64,0,0,8))
        self.vm=VM(self.art,temp.name,resource_budget=self.budget)

    def call(self,name,*args):
        return self.vm.call(self.ids['demo.'+name],list(args))

    def test_compiled_mapping_returns_checked_immutable_owner(self):
        import os
        import sys
        budget = ResourceBudget(ResourceLimits(64, 32, 2, 8))
        with VM(self.art, resource_budget=budget) as vm:
            owner = vm.call(self.ids['demo.map_bytes'], [b'abcdefgh'])
            if sys.platform != 'linux' or not hasattr(os, 'memfd_create'):
                self.assertIsInstance(owner, Record)
                return
            with vm.heap.pin(owner):
                self.assertEqual(vm.call(self.ids['demo.read'], [owner, 0, 8]), b'abcdefgh')
                self.assertIsInstance(vm.call(self.ids['demo.write'], [owner, 0, b'X']), Record)
                self.assertEqual(budget.snapshot()['used']['descriptors'], 1)
                view = vm.call(self.ids['demo.view'], [owner, 2, 4])
                with vm.heap.pin(view):
                    self.assertEqual(vm.call(self.ids['demo.read_view'], [view, 0, 4]), b'cdef')
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_mapping_constructor_failed_cleanup_is_retained_by_native(self):
        import os
        import sys
        from unittest.mock import patch
        from gopyt.resource_mapping import MappingStorage
        from gopyt.resource_natives import install
        if sys.platform != 'linux' or not hasattr(os, 'memfd_create'):
            self.skipTest('Linux sealed profile')
        budget = ResourceBudget(ResourceLimits(64, 32, 2, 8))
        vm = VM(self.art, resource_budget=budget)
        self.addCleanup(vm.close)
        table = {}
        install(table)
        original_close = MappingStorage.close
        attempts = []

        def close(storage):
            attempts.append(1)
            if len(attempts) <= 2:
                raise OSError('injected cleanup failure')
            original_close(storage)

        with patch('gopyt.resource_mapping.mmap.mmap', side_effect=OSError('map failed')):
            with patch.object(MappingStorage, 'close', close):
                result = table['data.buffer.map_bytes'](vm, [b'abcd'], None)
                self.assertIsInstance(result, Record)
                self.assertEqual(vm.heap.pending_resources(), 1)
                self.assertEqual(budget.snapshot()['used']['native_bytes'], 4)
                self.assertEqual(vm.heap.drain_resources(), 1)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_all_operations_and_opaque_union_dispatch(self):
        owner=self.call('allocate',8)
        with self.vm.heap.pin(owner):
            self.assertIs(self.call('write',owner,0,b'abcdefgh'),UNIT)
            parent=self.call('view',owner,1,6)
            with self.vm.heap.pin(parent):
                child=self.call('subview',parent,2,3)
                with self.vm.heap.pin(child):
                    self.assertEqual(self.call('read_view',child,0,3),b'def')
                    self.assertIs(self.call('close_view',parent),UNIT)
                    self.assertIs(self.call('write_view',child,0,b'XYZ'),UNIT)
                    self.assertEqual(self.call('read',owner,0,8),b'abcXYZgh')
                    self.assertIs(self.call('freeze',owner),UNIT)
                    self.assertIsInstance(self.call('write_view',child,0,b'!'),Record)
                    self.assertIs(self.call('close',owner),UNIT)
                    self.assertIsInstance(self.call('read_view',child,0,1),Record)
                    self.assertIs(self.call('close_view',child),UNIT)
        self.assertEqual(self.budget.snapshot()['active_reservations'],0)

    def test_exhaustion_bounds_and_foreign_heap_reject(self):
        self.assertIsInstance(self.call('allocate',65),Record)
        owner=self.call('allocate',8)
        with self.vm.heap.pin(owner):
            self.assertIsInstance(self.call('read',owner,-1,1),Record)
            foreign=VM(self.art,resource_budget=self.budget)
            with self.assertRaises(Trap):foreign.call(self.ids['demo.read'],[owner,0,1])
            foreign.heap.collect();foreign.heap.drain_resources()
            self.assertNotIn(id(owner),foreign.heap.objects)
            self.assertEqual(self.call('read',owner,0,1),b'\0')
            self.call('close',owner)
        self.assertEqual(self.budget.snapshot()['active_reservations'],0)

    def test_explicit_vm_close_invalidates_pinned_resources(self):
        owner=self.call('allocate',8)
        with self.vm.heap.pin(owner):
            view=self.call('view',owner,0,4)
            with self.vm.heap.pin(view):
                self.assertTrue(self.vm.close())
                self.assertTrue(self.vm.close())
                self.assertEqual(self.budget.snapshot()['active_reservations'],0)
                with self.assertRaises(RuntimeError):self.call('allocate',1)
        self.assertEqual(self.vm.heap.pending_resources(),0)

    def test_busy_vm_close_leaves_admitted_call_running(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        entered,finish=threading.Event(),threading.Event()
        original=self.vm._call_admitted
        def admitted(*args,**kwargs):
            entered.set()
            if not finish.wait(5):raise AssertionError('caller watchdog')
            return original(*args,**kwargs)
        with patch.object(self.vm,'_call_admitted',admitted), ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.call,'allocate',8)
            try:
                self.assertTrue(entered.wait(2))
                self.assertFalse(self.vm.close())
            finally:finish.set()
            owner=future.result(5)
        self.assertTrue(self.vm.close())
        self.assertEqual(self.budget.snapshot()['active_reservations'],0)

    def test_loader_rejects_forged_resource_types_and_effects(self):
        import copy
        from gopyt.gobyte import decode, encode
        from gopyt.diag import CompileError
        for mutation in ('record', 'unknown_opaque', 'unknown_effect', 'native_effect'):
            with self.subTest(mutation=mutation):
                art=copy.deepcopy(self.art)
                td=next(t for t in art.types if art.const_str(t.name)=='data.buffer.Buffer')
                if mutation=='record':td.kind=1
                elif mutation=='unknown_opaque':art.consts[td.name].value='rogue.Handle'
                elif mutation=='unknown_effect':art.funcs[0].effects |= 1 << 13
                else:
                    fn=next(f for f in art.funcs if art.const_str(f.name)=='data.buffer.allocate')
                    fn.effects=0
                with self.assertRaises(CompileError) as caught:
                    VM(decode(encode(art)))
                self.assertEqual(caught.exception.diag.code,100)

    def test_compiled_nested_union_match_dispatches_buffer_and_view(self):
        signature='task inspect(size: i64) -> bytes | ResourceError\n    effects { resource }'
        body=signature+'''
{
    found = data.buffer.allocate(size)
    return match found {
        Buffer -> inspect_view(found, size)
        ResourceError { message } -> ResourceError { message: message }
    }
}
'''
        view_signature='task inspect_view(buffer: Buffer, size: i64) -> bytes | ResourceError\n    effects { resource }'
        view_body=view_signature+'''
{
    found = data.buffer.view(buffer, 0, size)
    return match found {
        View -> data.buffer.read_view(found, 0, size)
        ResourceError { message } -> ResourceError { message: message }
    }
}
'''
        with tempfile.TemporaryDirectory() as root:
            uses='use core.status { ResourceError }\nuse data.buffer { Buffer, View, allocate, view, read_view }'
            files=module(signature+'\n\n'+view_signature,body+'\n'+view_body,uses=uses,
                spec_uses='use core.status { ResourceError }\nuse data.buffer { Buffer }')
            write_pkg(root,files,fmt=True)
            _,art,ids=build(root)
            with VM(art,root,resource_budget=self.budget) as vm:
                self.assertEqual(vm.call(ids['demo.inspect'],[4]),b'\0'*4)
                self.assertIsInstance(vm.call(ids['demo.inspect'],[65]),Record)
            self.assertEqual(self.budget.snapshot()['active_reservations'],0)
