"""Operational and review documents stay consistent with the frozen targets."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent
OPERATIONS = ROOT / 'docs' / 'operations.md'
PACKAGE = ROOT / 'docs' / 'security-review-package.md'
FREEZE = ROOT / 'validation' / 'workload-freeze' / 'targets.json'


def _frozen():
    return json.loads(FREEZE.read_text())['frozen']


class Operations(unittest.TestCase):
    def test_recovery_objectives_match_the_frozen_targets(self):
        text = OPERATIONS.read_text()
        budgets = _frozen()['budgets']
        # A runbook that drifts from the frozen objective would have an operator
        # drilling against a number nobody agreed to.
        self.assertIn(str(budgets['rto_seconds']), text)
        self.assertIn(str(budgets['rpo_committed_transactions_lost']), text)
        self.assertIn(str(budgets['latency_p99_ms']), text)

    def test_supported_platforms_match_the_frozen_envelope(self):
        text = OPERATIONS.read_text()
        envelope = _frozen()['envelope']
        self.assertIn(envelope['production_platform']['os'], text)
        for version in envelope['production_platform']['python']:
            self.assertIn(version, text)
        self.assertIn(envelope['development_only_platform']['ci_image'], text)
        for unsupported in ('Windows', '32-bit', 'non-glibc'):
            self.assertIn(unsupported, text)

    def test_release_ownership_is_named(self):
        text = OPERATIONS.read_text()
        for role in ('Release owner', 'Security contact', 'Deployment owner'):
            self.assertIn(role, text)
        # A single-maintainer project must not imply an on-call rotation.
        self.assertIn('single maintainer', text)
        self.assertIn('no availability commitment', text)

    def test_the_runbook_does_not_claim_measured_recovery(self):
        text = OPERATIONS.read_text()
        self.assertIn('Neither has been measured', text)
        self.assertIn('#25', text)


class ReviewPackage(unittest.TestCase):
    def test_it_refuses_to_present_itself_as_a_review(self):
        text = PACKAGE.read_text()
        self.assertIn('It is a package for a review, not a review',
                      (ROOT / 'docs' / 'README.md').read_text())
        self.assertIn('does not satisfy independence', text)
        self.assertIn('unreviewed', text)

    def test_every_named_source_file_exists(self):
        text = PACKAGE.read_text()
        named = set(re.findall(r'`(gopyt/[a-z_]+\.py|tools/[a-z_]+\.py|requirements/[a-z.]+)`', text))
        self.assertTrue(named)
        for name in sorted(named):
            self.assertTrue((ROOT / name).is_file(), name)

    def test_every_named_document_exists(self):
        text = PACKAGE.read_text()
        for name in re.findall(r'\]\(([a-z0-9\-]+\.md)\)', text):
            self.assertTrue((ROOT / 'docs' / name).is_file(), name)

    def test_the_known_gaps_match_the_frozen_ones(self):
        text = PACKAGE.read_text()
        # The reviewer must not have to rediscover a gap the project already knows.
        for gap in _frozen()['known_gaps']:
            self.assertIn('#' + str(gap['issue']), text)
