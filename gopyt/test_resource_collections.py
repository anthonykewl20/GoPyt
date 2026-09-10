"""Collection ownership and partial-construction failure regressions."""
import unittest
from gopyt.resource_budget import ResourceBudget, ResourceLimits, ResourceLimitError
from gopyt.resource_collections import copy_list, append_list, range_list


class CollectionAdmission(unittest.TestCase):
    def budget(self, size=65536):
        return ResourceBudget(ResourceLimits(size, 0, 0, 0))

    def test_copy_and_append_preserve_inputs_and_alias_ownership(self):
        budget = self.budget()
        child = ['borrowed']
        source = [child]
        result = append_list(source, 42, budget)
        self.assertEqual(result, [child, 42])
        self.assertEqual(source, [child])
        self.assertIs(result[0], child)
        alias = result
        del result
        self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
        del alias
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_range_integer_alias_outlives_container(self):
        budget = self.budget()
        result = range_list(1000, 1020, budget)
        self.assertEqual(result, list(range(1000, 1020)))
        value = result[7]
        del result
        self.assertEqual(value, 1007)
        self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
        del value
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_capacity_rejection_retained_traceback_does_not_keep_partial_output(self):
        for producer in (lambda b: copy_list(list(range(20)), b),
                         lambda b: range_list(1000, 1020, b)):
            for capacity in range(0, 700, 17):
                budget = self.budget(capacity)
                failure = None
                try:
                    result = producer(budget)
                except ResourceLimitError as error:
                    failure = error
                else:
                    del result
                self.assertEqual(budget.snapshot()['active_reservations'], 0,
                                 (capacity, failure))

    def test_cancellation_at_every_reached_checkpoint_releases_partial_output(self):
        class Stop(Exception):
            pass
        for producer in (lambda b, c: copy_list(list(range(20)), b, c),
                         lambda b, c: range_list(1000, 1020, b, c)):
            checks = []
            result = producer(self.budget(), lambda: checks.append(1))
            del result
            for target in range(1, len(checks) + 1):
                budget = self.budget()
                count = 0
                def check():
                    nonlocal count
                    count += 1
                    if count == target:
                        raise Stop()
                failure = None
                try:
                    producer(budget, check)
                except Stop as error:
                    failure = error
                self.assertIsNotNone(failure)
                self.assertEqual(budget.snapshot()['active_reservations'], 0, target)


class CompiledLists(unittest.TestCase):
    def setUp(self):
        import tempfile
        from gopyt.cli import build
        from gopyt.testing import write_pkg
        from gopyt.test_vm import module
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        signature = 'fn build() -> list[i64]'
        files = module(signature, signature + '''
{
    original = core.list.range(1000, 1003)
    extended = core.list.append(original, 1003)
    return core.list.append(extended, core.list.len(original))
}
''', uses='use core.list { range, append, len }')
        write_pkg(temp.name, files, fmt=True)
        _, self.art, self.ids = build(temp.name)

    def test_compiled_native_results_and_scalar_aliases_retain_charges(self):
        from gopyt.vm import VM
        budget = ResourceBudget(ResourceLimits(65536, 0, 0, 0))
        with VM(self.art, resource_budget=budget) as vm:
            result = vm.call(self.ids['demo.build'], [])
            with vm.heap.pin(result):
                vm.heap.collect()
                self.assertEqual(result, [1000, 1001, 1002, 1003, 3])
                scalar = result[1]
                self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
        self.assertEqual(result, [])
        del result
        self.assertEqual(scalar, 1001)
        self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
        del scalar
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_compiled_exhaustion_is_allocation_trap_and_unwinds_owned_output(self):
        from gopyt.vm import VM, Trap
        from gopyt import ops
        budget = ResourceBudget(ResourceLimits(31, 0, 0, 0))
        failure = None
        with VM(self.art, resource_budget=budget) as vm:
            try:
                vm.call(self.ids['demo.build'], [])
            except Trap as error:
                failure = error
        self.assertIsNotNone(failure)
        self.assertEqual(failure.code, ops.TRAP_ALLOC)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_verified_list_opcodes_admit_copies_before_allocation(self):
        import copy
        import struct
        from gopyt import gobyte, ops
        from gopyt.vm import VM, Trap
        art = copy.deepcopy(self.art)
        first = next(i for i, c in enumerate(art.consts)
                     if c.tag == gobyte.TAG_I64 and c.value == 1000)
        last = next(i for i, c in enumerate(art.consts)
                    if c.tag == gobyte.TAG_I64 and c.value == 1003)
        art.funcs[self.ids['demo.build']].code = (
            bytes([ops.CONST]) + struct.pack('<I', first) +
            bytes([ops.NEW_LIST]) + struct.pack('<H', 1) +
            bytes([ops.CONST]) + struct.pack('<I', last) +
            bytes([ops.LIST_APPEND, ops.RETURN]))
        art = gobyte.decode(gobyte.encode(art))
        for capacity, succeeds in ((31, False), (65536, True)):
            budget = ResourceBudget(ResourceLimits(capacity, 0, 0, 0))
            with VM(art, resource_budget=budget) as vm:
                if succeeds:
                    result = vm.call(self.ids['demo.build'], [])
                    self.assertEqual(result, [1000, 1003])
                    self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
                else:
                    with self.assertRaises(Trap) as caught:
                        vm.call(self.ids['demo.build'], [])
                    self.assertEqual(caught.exception.code, ops.TRAP_ALLOC)
            if succeeds:
                del result
            self.assertEqual(budget.snapshot()['active_reservations'], 0)


class MapAdmission(unittest.TestCase):
    def budget(self, size=65536):
        return ResourceBudget(ResourceLimits(size, 0, 0, 0))

    def test_persistent_update_and_utf8_order_match_oracle(self):
        from gopyt.resource_collections import set_map, map_keys
        budget = self.budget()
        source = {'z': 1, 'é': 2, '中': 3, '\U0001f600': 4, '\ue000': 5}
        result = set_map(source, 'é', 9, budget)
        self.assertEqual(source['é'], 2)
        self.assertEqual(result['é'], 9)
        keys = map_keys(result, budget)
        self.assertEqual(keys, sorted(source, key=lambda x: x.encode('utf-8')))
        del result
        self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
        del keys
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_retained_capacity_and_cancellation_failures_release_destinations(self):
        from gopyt.resource_collections import set_map, map_keys
        source = {str(i): i for i in range(20)}
        producers = (lambda b, c: set_map(source, 'new', 21, b, c),
                     lambda b, c: map_keys(source, b, c))
        class Stop(Exception):
            pass
        for producer in producers:
            for capacity in range(0, 3000, 31):
                budget = self.budget(capacity)
                failure = None
                try:
                    result = producer(budget, lambda: None)
                except ResourceLimitError as error:
                    failure = error
                else:
                    del result
                self.assertEqual(budget.snapshot()['active_reservations'], 0,
                                 (capacity, failure))
            checks = []
            result = producer(self.budget(), lambda: checks.append(1))
            del result
            for target in range(1, len(checks) + 1):
                budget = self.budget()
                count = 0
                def check():
                    nonlocal count
                    count += 1
                    if count == target:
                        raise Stop()
                failure = None
                try:
                    producer(budget, check)
                except Stop as error:
                    failure = error
                self.assertIsNotNone(failure)
                self.assertEqual(budget.snapshot()['active_reservations'], 0, target)

    def test_existing_key_layout_transition_is_admitted(self):
        from gopyt.resource_collections import OwnedMap
        class Text(str):
            pass
        budget = self.budget()
        result = OwnedMap(budget)
        result.set_owned('key', 1)
        before = budget.snapshot()['used']['native_bytes']
        result.set_owned(Text('key'), 2)
        self.assertEqual(result, {'key': 2})
        self.assertGreaterEqual(budget.snapshot()['peak']['native_bytes'], 2 * before)
        del result
        self.assertEqual(budget.snapshot()['active_reservations'], 0)


class CompiledMaps(unittest.TestCase):
    def test_compiled_map_copy_sort_and_budget_rejection(self):
        import tempfile
        from gopyt.cli import build
        from gopyt.testing import write_pkg
        from gopyt.test_vm import module
        from gopyt.vm import VM, Trap
        from gopyt import ops
        signature = 'fn build() -> list[i64]'
        files = module(signature, signature + '''
{
    empty = core.map.empty[i64, i64]()
    first = core.map.set(empty, 9, 1)
    second = core.map.set(first, -3, 2)
    third = core.map.set(second, 9, 3)
    return core.map.keys(third)
}
''', uses='use core.map { empty, set, keys }')
        with tempfile.TemporaryDirectory() as root:
            write_pkg(root, files, fmt=True)
            _, art, ids = build(root)
            for capacity, succeeds in ((255, False), (65536, True)):
                budget = ResourceBudget(ResourceLimits(capacity, 0, 0, 0))
                with VM(art, resource_budget=budget) as vm:
                    if succeeds:
                        result = vm.call(ids['demo.build'], [])
                        self.assertEqual(result, [-3, 9])
                        self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
                    else:
                        with self.assertRaises(Trap) as caught:
                            vm.call(ids['demo.build'], [])
                        self.assertEqual(caught.exception.code, ops.TRAP_ALLOC)
                if succeeds:
                    del result
                self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_verified_map_opcodes_admit_destination(self):
        import tempfile
        import struct
        from gopyt import gobyte, ops
        from gopyt.cli import build
        from gopyt.testing import write_pkg
        from gopyt.test_vm import module
        from gopyt.vm import VM, Trap
        signature = 'fn build() -> map[i64, i64]'
        files = module(signature, signature + '''
{
    return core.map.set(core.map.empty[i64, i64](), 9, 1)
}
''', uses='use core.map { empty, set }')
        with tempfile.TemporaryDirectory() as root:
            write_pkg(root, files, fmt=True)
            _, art, ids = build(root)
            constants = {c.value: i for i, c in enumerate(art.consts)
                         if c.tag == gobyte.TAG_I64}
            art.funcs[ids['demo.build']].code = (
                bytes([ops.NEW_MAP, ops.CONST]) + struct.pack('<I', constants[9]) +
                bytes([ops.CONST]) + struct.pack('<I', constants[1]) +
                bytes([ops.MAP_SET, ops.RETURN]))
            art = gobyte.decode(gobyte.encode(art))
            for capacity, succeeds in ((255, False), (65536, True)):
                budget = ResourceBudget(ResourceLimits(capacity, 0, 0, 0))
                with VM(art, resource_budget=budget) as vm:
                    if succeeds:
                        result = vm.call(ids['demo.build'], [])
                        self.assertEqual(result, {9: 1})
                    else:
                        with self.assertRaises(Trap) as caught:
                            vm.call(ids['demo.build'], [])
                        self.assertEqual(caught.exception.code, ops.TRAP_ALLOC)
                if succeeds:
                    del result
                self.assertEqual(budget.snapshot()['active_reservations'], 0)
