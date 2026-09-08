"""Preparation must be bounded and must not bind a changing engine."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from benchmarks.language_guard.campaign import fixture,DOMAINS
from gopyt.guard import MAX_BYTES,main,prepare_bundle

class PreparationLimits(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'policy';self.root.mkdir()
        self.cases=fixture(self.root,DOMAINS[0]);(self.root/'.gopyt-transaction.lock').unlink(missing_ok=True)

    def test_case_limit_is_checked_before_compilation(self):
        with patch('gopyt.guard._check_policy',side_effect=AssertionError('compiler ran')):
            with self.assertRaisesRegex(ValueError,'10000'):
                prepare_bundle(self.root,['impl/inventory.gopyt'],[self.cases[0]]*10001,{'rule':'Inventory.'})

    def test_engine_change_during_preparation_is_refused(self):
        with patch('gopyt.guard.engine_digest',side_effect=['0'*64,'1'*64]):
            with self.assertRaisesRegex(ValueError,'engine changed during preparation'):
                prepare_bundle(self.root,['impl/inventory.gopyt'],self.cases,{'rule':'Inventory.'})

    def test_cli_oversized_policy_refused_before_json_parse(self):
        path=Path(self.temp.name)/'large.json';path.write_bytes(b' '*(MAX_BYTES+1))
        out=Path(self.temp.name)/'out.json'
        with patch('gopyt.guard.unique_json',side_effect=AssertionError('JSON parser ran')):
            self.assertEqual(main(['prepare','--candidate',str(self.root),'--policy',str(path),'--out',str(out)]),1)
        self.assertFalse(out.exists())
