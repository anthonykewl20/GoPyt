"""Owned nominal field storage remains reachable through its record or enum."""
import struct
import unittest
from gopyt.heap import Heap
from gopyt.values import Record, EnumVal
from gopyt.resource_collections import copy_list
from gopyt.resource_budget import ResourceBudget, ResourceLimits


class FieldTracing(unittest.TestCase):
    def test_parent_pin_preserves_previously_pinned_owned_fields(self):
        for constructor in (lambda f: Record(0, f), lambda f: EnumVal(0, 0, f)):
            budget = ResourceBudget(ResourceLimits(1024, 0, 0, 0))
            heap = Heap()
            fields = copy_list(['payload'], budget)
            parent = constructor(fields)
            with heap.pin(parent):
                with heap.pin(fields):
                    pass
                heap.collect()
                self.assertEqual(parent.fields, ['payload'])
                self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
            heap.collect()
            self.assertEqual(fields, [])
            del fields, parent
            self.assertEqual(budget.snapshot()['active_reservations'], 0)


class CompiledFields(unittest.TestCase):
    def setUp(self):
        import tempfile
        from gopyt.cli import build
        from gopyt.testing import write_pkg
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        write_pkg(temp.name, {
            'spec/demo.gopyt': '''module demo

type Pair {
    left: i64
    right: i64
}
enum Choice {
    Empty
    Full {
        value: i64
    }
}
fn pair(value: i64) -> Pair
fn full(value: i64) -> Choice
fn empty() -> Choice
''',
            'impl/demo.gopyt': '''module demo
fn pair(value: i64) -> Pair
{
    return Pair {
        left: value
        right: value + 1
    }
}
fn full(value: i64) -> Choice
{
    return Choice.Full {
        value: value
    }
}
fn empty() -> Choice
{
    return Choice.Empty
}
''',
        }, fmt=True)
        _, self.art, self.ids = build(temp.name)

    def test_compiled_fields_are_owned_and_parent_pin_marks_the_array(self):
        from gopyt.vm import VM
        for name, expected in (('pair', [7, 8]), ('full', [7])):
            budget = ResourceBudget(ResourceLimits(1024, 0, 0, 0))
            with VM(self.art, resource_budget=budget) as vm:
                parent = vm.call(self.ids['demo.'+name], [7])
                with vm.heap.pin(parent):
                    fields = parent.fields
                    with vm.heap.pin(fields):
                        pass
                    vm.heap.collect()
                    self.assertEqual(parent.fields, expected)
                    self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
            self.assertEqual(parent.fields, [])
            del fields, parent
            self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_field_capacity_rejection_and_zero_field_variant(self):
        from gopyt.vm import VM, Trap
        from gopyt import ops
        for name in ('pair', 'full'):
            budget = ResourceBudget(ResourceLimits(4 * struct.calcsize('P') - 1, 0, 0, 0))
            with VM(self.art, resource_budget=budget) as vm:
                with self.assertRaises(Trap) as caught:
                    vm.call(self.ids['demo.'+name], [7])
                self.assertEqual(caught.exception.code, ops.TRAP_ALLOC)
            self.assertEqual(budget.snapshot()['active_reservations'], 0)
        budget = ResourceBudget(ResourceLimits(0, 0, 0, 0))
        with VM(self.art, resource_budget=budget) as vm:
            result = vm.call(self.ids['demo.empty'], [])
            self.assertEqual(result.fields, [])
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_budget_refusal_sweeps_dead_field_arrays_before_trapping(self):
        """Byte pressure is independent of the heap's object-count threshold."""
        from gopyt.vm import VM
        slot = ((1 + (1 >> 3) + 6) & ~3) * struct.calcsize('P')
        budget = ResourceBudget(ResourceLimits(slot, 0, 0, 0))
        with VM(self.art, resource_budget=budget) as vm:
            first = vm.call(self.ids['demo.full'], [7])
            self.assertEqual(first.fields, [7])
            del first
            vm.heap.release_result()  # the host has consumed the first result
            # Only one field array fits; the refusal must sweep the dead one.
            second = vm.call(self.ids['demo.full'], [8])
            self.assertEqual(second.fields, [8])
            self.assertEqual(budget.snapshot()['used']['native_bytes'], slot)
            del second
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_budget_refusal_is_overload_and_the_fixed_ceiling_is_not(self):
        from gopyt.vm import VM, Trap
        from gopyt import ops
        budget = ResourceBudget(ResourceLimits(4 * struct.calcsize('P') - 1, 0, 0, 0))
        with VM(self.art, resource_budget=budget) as vm:
            with self.assertRaises(Trap) as caught:
                vm.call(self.ids['demo.full'], [7])
        self.assertEqual(caught.exception.code, ops.TRAP_ALLOC)
        self.assertTrue(caught.exception.overload)
        self.assertFalse(Trap(ops.TRAP_ALLOC).overload)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_cancelled_field_construction_releases_partial_array(self):
        from unittest.mock import patch
        from gopyt.vm import VM, Cancelled
        for name in ('pair', 'full'):
            budget = ResourceBudget(ResourceLimits(1024, 0, 0, 0))
            failure = None
            with VM(self.art, resource_budget=budget) as vm:
                def check():
                    if budget.snapshot()['used']['native_bytes']:
                        raise Cancelled()
                with patch.object(vm, 'check_cancelled', side_effect=check):
                    try:
                        vm.call(self.ids['demo.'+name], [7])
                    except Cancelled as error:
                        failure = error
            self.assertIsNotNone(failure)
            self.assertEqual(budget.snapshot()['active_reservations'], 0)
