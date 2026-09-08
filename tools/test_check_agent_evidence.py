"""Regression checks for evidence acceptance/rejection, without agents or subjects."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools.check_agent_evidence import Audit, command_pass, event_metrics, inventory, unique_object


class EvidenceChecks(unittest.TestCase):
    def test_stream_deduplicates_tool_items_and_preserves_missing_usage(self):
        events = [
            {'type': 'thread.started', 'thread_id': 'fresh'},
            {'type': 'item.started', 'item': {'id': 'x', 'type': 'command_execution'}},
            {'type': 'item.completed', 'item': {'id': 'x', 'type': 'command_execution'}},
            {'type': 'item.completed', 'item': {'id': 'error', 'type': 'error'}},
            {'type': 'turn.failed'},
        ]
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, 'events.jsonl')
            path.write_text('\n'.join(json.dumps(event) for event in events))
            result = event_metrics(path)
        self.assertEqual(result['observed_tool_items'], 1)
        self.assertIsNone(result['usage'])
        self.assertEqual(result['terminal_event'], 'turn.failed')
        self.assertEqual(result['thread_ids'], ['fresh'])

    def test_reported_usage_is_read_from_the_terminal_event(self):
        usage = {'input_tokens': 50, 'cached_input_tokens': 12, 'output_tokens': 8}
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, 'events.jsonl')
            path.write_text(json.dumps({'type': 'turn.completed', 'usage': usage}))
            self.assertEqual(event_metrics(path)['usage'], usage)

    def test_timeout_or_nonzero_exit_cannot_pass_even_with_valid_stdout(self):
        self.assertTrue(command_pass({'returncode': 0, 'timed_out': False}))
        for result in ({'returncode': 0, 'timed_out': True}, {'returncode': 1},
                       {'returncode': False}, {'returncode': None}):
            self.assertFalse(command_pass(result))

    def evaluation(self, behavior, exit_code):
        ordinary = {'command': ['check'], 'returncode': 0, 'pass': True, 'timed_out': False}
        return {'static': ordinary, 'public': ordinary, 'native': None, 'behavior': behavior,
                'oracle_process': {'command': ['oracle'], 'returncode': exit_code,
                                   'pass': exit_code == 0, 'timed_out': False,
                                   'stdout': json.dumps(behavior)}}

    def test_honest_oracle_launch_failure_is_consistent_evidence(self):
        behavior = {'schema': 'quotation-v1-business-report', 'business_pass': False,
                    'errors': ['launch failed: missing interpreter'], 'case_count': 1202,
                    'groups': {}, 'failures': []}
        audit = Audit(Path('/unused'))
        audit.check_commands(self.evaluation(behavior, 1), 'failure',
                             {'static': ['check'], 'public': ['check'], 'adapter': ['adapter']})
        self.assertEqual(audit.errors, [])

    def test_scored_failure_is_consistent_but_falsified_pass_is_rejected(self):
        behavior = {'schema': 'quotation-v1-business-report', 'business_pass': False,
                    'command': ['adapter'], 'errors': [], 'case_count': 2, 'passed': 1,
                    'groups': {'regression': {'total': 2, 'passed': 1}},
                    'failures': [{'reason': 'wrong formula'}], 'regression_pass': False}
        evaluation = self.evaluation(behavior, 1)
        commands = {'static': ['check'], 'public': ['check'], 'adapter': ['adapter']}
        audit = Audit(Path('/unused'))
        audit.check_commands(evaluation, 'honest', commands)
        self.assertEqual(audit.errors, [])
        falsified = copy.deepcopy(evaluation)
        falsified['behavior']['business_pass'] = True
        falsified['oracle_process']['stdout'] = json.dumps(falsified['behavior'])
        audit = Audit(Path('/unused'))
        audit.check_commands(falsified, 'falsified', commands)
        self.assertTrue(any('business gate disagrees' in error for error in audit.errors))

    def test_inventory_skips_generated_cache_but_records_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            (path / 'model.py').write_text('pass\n')
            (path / '__pycache__').mkdir()
            (path / '__pycache__/generated.pyc').write_bytes(b'cache')
            (path / 'alias').symlink_to('model.py')
            result = inventory(path)
        self.assertEqual(set(result), {'model.py', 'alias'})
        self.assertEqual(result['alias'], 'symlink:model.py')

    def test_duplicate_clarification_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            json.loads('{"status":"needs_clarification","status":"other"}', object_pairs_hook=unique_object)


if __name__ == '__main__':
    unittest.main()
