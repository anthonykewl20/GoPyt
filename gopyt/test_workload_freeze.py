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
        # what any measurement covers; those gaps must stay recorded, not quietly
        # dropped, until their issues close.
        self.assertLessEqual({5, 6, 8, 24, 25, 26}, gaps)

    def test_no_measurement_is_claimed(self):
        text = (ROOT / 'docs' / 'production-qualification-targets.md').read_text()
        self.assertIn('They are\nrequirements, not results.', text)
