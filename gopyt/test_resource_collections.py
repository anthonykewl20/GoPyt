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
