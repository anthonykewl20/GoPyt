"""The frozen workload, threat model and qualification targets stay intact."""
import json
from pathlib import Path
import unittest

from tools.check_workload_freeze import check, _digest

ROOT = Path(__file__).resolve().parent.parent
FREEZE = ROOT / 'validation' / 'workload-freeze' / 'targets.json'


class WorkloadFreeze(unittest.TestCase):
    def test_frozen_targets_verify(self):
        self.assertEqual(check(ROOT), [])

    def test_tampered_threshold_is_detected(self):
        document = json.loads(FREEZE.read_text())
        document['frozen']['budgets']['latency_p99_ms'] = 5000
        self.assertNotEqual(document['digest'], _digest(document))

    def test_recorded_digest_covers_every_frozen_section(self):
        document = json.loads(FREEZE.read_text())
        baseline = _digest(document)
        for section in sorted(document['frozen']):
            altered = json.loads(FREEZE.read_text())
            altered['frozen'].pop(section)
            self.assertNotEqual(_digest(altered), baseline, section)

    def test_the_freeze_names_its_unmet_gaps(self):
        document = json.loads(FREEZE.read_text())
        gaps = {gap['issue'] for gap in document['frozen']['known_gaps']}
        # The envelope deliberately exceeds what the shipped backend can hold and
        # what any measurement covers. These gaps must stay recorded, not quietly
        # dropped: a gap leaves this list only when its issue closes, and only
        # through a revision bump that records why.
        self.assertLessEqual({5, 6, 8, 25, 26}, gaps)
        self.assertGreaterEqual(document['revision'], 2)
        # Every gap must say what is still unmet, in its own words.
        for gap in document['frozen']['known_gaps']:
            self.assertTrue(gap.get('gap'), gap['issue'])

    def test_a_removed_gap_carries_its_reason(self):
        document = json.loads(FREEZE.read_text())
        if document['revision'] > 1:
            self.assertTrue(document['revision_reason'])
            # Removing a gap must not be how a target quietly changes.
            self.assertIn('No target, threshold, dataset identity or envelope '
                          'value changed.', document['revision_reason'])

    def test_no_measurement_is_claimed(self):
        text = (ROOT / 'docs' / 'production-qualification-targets.md').read_text()
        self.assertIn('They are\nrequirements, not results.', text)
