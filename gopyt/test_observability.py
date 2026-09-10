"""Operational metrics stay closed, exact, bounded and payload-free."""
import json
import os
import pathlib
import re
import unittest
from unittest.mock import patch

from gopyt.observe import METRICS, METRIC_NAMES, Observe, REFUSAL_METRICS
from gopyt.resource_budget import ResourceBudget, ResourceLimits, ResourceLimitError


class ClosedMetricSet(unittest.TestCase):
    def test_a_name_outside_the_set_is_refused(self):
        observe = Observe()
        # Telemetry size must not follow traffic: a metric keyed on a request
        # value would let one caller multiply this table.
        for name in ('deny:' + 'x' * 64, 'http:/orders/42:200', 'alloc:', ''):
            with self.subTest(name=name):
                with self.assertRaises(KeyError):
                    observe.count(name)
        self.assertEqual(sorted(observe.metrics()['counters']), sorted(METRICS))

    def test_every_name_is_a_lowercase_family_and_kind(self):
        for name in METRICS:
            with self.subTest(name=name):
                self.assertRegex(name, r'^[a-z]+:[a-z_]+$')
        self.assertEqual(len(METRIC_NAMES), len(METRICS))

    def test_counters_are_exact_and_saturate(self):
        observe = Observe()
        for _ in range(5):
            observe.count('queue:worker_refused')
        self.assertEqual(observe.metrics()['counters']['queue:worker_refused'], 5)
        observe.count('queue:worker_refused', 2 ** 64)
        self.assertEqual(observe.metrics()['counters']['queue:worker_refused'], 2 ** 64 - 1)

    def test_the_table_does_not_grow_with_events(self):
        observe = Observe()
        before = observe.metrics()['memory_cells']
        for index in range(2000):
            observe.deny('rate')
            observe.event(f'outcome:route-{index}', False, 1.0, task=f'task-{index}')
        after = observe.metrics()
        self.assertEqual(after['memory_cells'], before)
        self.assertEqual(len(after['counters']), len(METRICS))


class DenialCoverage(unittest.TestCase):
    def test_every_denial_kind_has_a_counter(self):
        observe = Observe()
        kinds = ('resource', 'secret', 'limit', 'egress', 'rate', 'database',
                 'identity', 'service_token', 'gateway', 'forwarded_identity')
        for kind in kinds:
            observe.deny(kind)
        counters = observe.metrics()['counters']
        for kind in kinds:
            self.assertEqual(counters['deny:' + kind], 1, kind)

    def test_the_wired_denials_match_the_declared_ones(self):
        """A denial emitted in the runtime but absent here would not be counted."""
        root = os.path.dirname(os.path.abspath(__file__))
        emitted = set()
        for name in sorted(os.listdir(root)):
            if not name.endswith('.py') or name.startswith('test_'):
                continue
            text = pathlib.Path(root, name).read_text(encoding='utf-8')
            emitted.update(re.findall(r"observe\.deny\(['\"]([a-z_]+)['\"]", text))
        self.assertTrue(emitted)
        for kind in sorted(emitted):
            self.assertIn('deny:' + kind, METRIC_NAMES, kind)


class AllocationPressure(unittest.TestCase):
    def test_a_refusal_names_the_field_that_ran_out(self):
        budget = ResourceBudget(ResourceLimits(8, 0, 0, 0))
        with self.assertRaises(ResourceLimitError) as caught:
            budget.reserve(native_bytes=64)
        self.assertEqual(caught.exception.fields, ('native_bytes',))
        with self.assertRaises(ResourceLimitError) as caught:
            budget.reserve(native_bytes=64, descriptors=1)
        self.assertEqual(set(caught.exception.fields), {'native_bytes', 'descriptors'})

    def test_each_budget_field_maps_to_a_declared_counter(self):
        observe = Observe()
        for field, metric in REFUSAL_METRICS.items():
            self.assertIn(metric, METRIC_NAMES, field)
        observe.refused(('native_bytes', 'descriptors', 'mapped_bytes', 'handles'))
        counters = observe.metrics()['counters']
        for metric in REFUSAL_METRICS.values():
            self.assertEqual(counters[metric], 1, metric)

    def test_an_unknown_field_is_ignored_rather_than_counted(self):
        observe = Observe()
        observe.refused(('native_bytes', 'something_new'))
        counters = observe.metrics()['counters']
        self.assertEqual(counters['alloc:refused_bytes'], 1)
        self.assertEqual(sum(counters.values()), 1)


class Redaction(unittest.TestCase):
    """What telemetry may carry, established rather than assumed.

    Two kinds of text reach this module. Task and trap names come from the
    compiled artifact, so they are declared identifiers. A `core.observe.note`
    tag comes from the application and is the only caller-supplied string, so
    it is the one that must never be emitted.
    """

    SECRET = 'operator-key-material-9f2a'

    def _emitted(self, observe):
        return json.dumps([observe.metrics(), observe.snapshot()])

    def test_an_application_note_never_reaches_any_output(self):
        observe = Observe()
        observe.note(self.SECRET)
        # It updated the sketches, so the event was recorded...
        self.assertEqual(observe.metrics()['events'], 1)
        # ...and it is not recoverable from anything this module emits.
        self.assertNotIn(self.SECRET, self._emitted(observe))

    def test_a_note_cannot_reach_the_failure_samples(self):
        observe = Observe()
        for index in range(200):
            observe.note(f'{self.SECRET}-{index}')
        self.assertEqual(observe.snapshot(), [])
        self.assertNotIn(self.SECRET, self._emitted(observe))

    def test_sample_task_names_come_from_the_compiled_artifact(self):
        """The one place a name is emitted, it is a declared function name."""
        import tempfile
        from gopyt.cli import build
        from gopyt.testing import write_pkg
        from gopyt.vm import VM, Trap
        source = {
            'spec/telemetry.gopyt': (
                'module telemetry\n\n'
                'fn always_traps(value: i64) -> i64\n'
                '    requires value > 0\n'
            ),
            'impl/telemetry.gopyt': (
                'module telemetry\n\n'
                'fn always_traps(value: i64) -> i64\n'
                '    requires value > 0\n{\n    return value\n}\n'
            ),
        }
        with tempfile.TemporaryDirectory(prefix='gopyt-telemetry-') as root:
            write_pkg(root, source)
            _program, art, ids = build(root)
            with VM(art, root) as vm:
                with self.assertRaises(Trap):
                    vm.call(ids['telemetry.always_traps'], [0])
                rows = vm.observe.snapshot()
                metrics = vm.observe.metrics()
        self.assertTrue(rows)
        self.assertEqual({row['task'] for row in rows}, {'telemetry.always_traps'})
        self.assertEqual(metrics['counters']['contract:precondition'], 1)

    def test_metrics_carry_no_free_text_at_all(self):
        observe = Observe()
        observe.note('anything')
        observe.outcome('some.task', 'core.status.DbError', 2.0)
        metrics = observe.metrics()
        self.assertEqual(sorted(metrics['counters']), sorted(METRICS))
        self.assertEqual(sorted(metrics), sorted(
            ['schema', 'events', 'failures', 'latency_ms_mean', 'latency_ms_variance',
             'anomaly_alarm', 'counters', 'memory_cells', 'scope']))

    def test_failure_samples_keep_only_task_tag_and_code(self):
        observe = Observe()
        observe.outcome('billing.charge', 'core.status.DbError', 3.0)
        rows = observe.snapshot()
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(sorted(row), ['code', 'tag', 'task'])

    def test_an_oversized_name_is_reduced_to_a_digest(self):
        observe = Observe()
        observe.outcome('t' * 4096, 'core.status.DbError', 1.0)
        emitted = self._emitted(observe)
        self.assertNotIn('t' * 4096, emitted)
        self.assertIn('sha256:', emitted)


class ContractFailures(unittest.TestCase):
    def test_contract_traps_are_counted_apart_from_other_traps(self):
        observe = Observe()
        observe.trap(1, 'requires')
        observe.trap(2, 'ensures')
        observe.trap(14, 'alloc')
        counters = observe.metrics()['counters']
        self.assertEqual(counters['contract:precondition'], 1)
        self.assertEqual(counters['contract:postcondition'], 1)
        # A non-contract trap must not land in a contract counter.
        self.assertEqual(sum(value for name, value in counters.items()
                             if name.startswith('contract:')), 2)

    def test_a_counting_failure_never_replaces_the_trap(self):
        observe = Observe()
        with patch.object(observe, 'count', side_effect=RuntimeError('telemetry down')):
            self.assertTrue(observe.trap(1, 'requires'))
