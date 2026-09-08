"""A pinned malformed policy must not be confused with a valid approval."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from gopyt.guard import canonical,digest,evaluate,main,prepare_bundle,validate_bundle
from benchmarks.language_guard.campaign import fixture,DOMAINS


class BundleValidation(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.pkg=self.root/'project';self.pkg.mkdir()
        self.cases=fixture(self.pkg,DOMAINS[0])
        (self.pkg/'.gopyt-transaction.lock').unlink(missing_ok=True)
        self.policy={'mutable':['impl/inventory.gopyt'],'cases':self.cases,'obligations':{'stock':'Reservation preserves nonnegative stock.'}}
        self.raw=prepare_bundle(self.pkg,**self.policy)

    def reject(self,edit):
        value=json.loads(self.raw);edit(value);raw=canonical(value)
        result=evaluate(raw,digest(raw),self.pkg)
        self.assertFalse(result['accepted'],result)
        self.assertIn('error',result)

    def test_empty_cases_even_with_correct_pin(self):
        self.reject(lambda b:b.update(cases=[]))

    def test_only_trap_cases_are_not_sufficient(self):
        self.reject(lambda b:b.update(cases=[{'symbol':'inventory.reserve','args':[0,1],'trap':1}]))

    def test_mutable_authority_cannot_expand_to_specs(self):
        self.reject(lambda b:b['mutable'].append('spec/inventory.gopyt'))

    def test_ambiguous_and_invalid_case_outcomes(self):
        for field,value in [('trap',1),('expected',1.0),('args',[True,2**63]),('symbol','../outside')]:
            with self.subTest(field=field,value=value):
                self.reject(lambda b:b['cases'][0].update({field:value}))

    def test_duplicate_keys_refused(self):
        raw=self.raw[:-1]+b',"cases":[]}'
        result=evaluate(raw,digest(raw),self.pkg)
        self.assertFalse(result['accepted'])
        self.assertIn('duplicate',result['error'])

    def test_empty_obligations_and_unknown_fields_refused(self):
        self.reject(lambda b:b.update(obligations={}))
        self.reject(lambda b:b.update(approved=True))

    def test_preparation_refuses_invalid_case(self):
        with self.assertRaises(ValueError):
            prepare_bundle(self.pkg,self.policy['mutable'],[],self.policy['obligations'])

    def test_inventory_path_traversal_and_wrong_hash_refused(self):
        self.reject(lambda b:b['files'].update({'../secret.gopyt':'0'*64}))
        self.reject(lambda b:b['files'].update({'spec/inventory.gopyt':'not-a-hash'}))

    def test_cli_prepare_and_evaluate_preserve_existing_receipts(self):
        config=self.root/'policy.json';config.write_bytes(canonical(self.policy))
        frozen=self.root/'bundle.json';receipt=self.root/'receipt.json'
        self.assertEqual(main(['prepare','--candidate',str(self.pkg),'--policy',str(config),'--out',str(frozen)]),0)
        self.assertEqual(main(['--candidate',str(self.pkg),'--bundle',str(frozen),
                               '--expected-sha256',digest(frozen.read_bytes()),'--receipt',str(receipt)]),0)
        before=receipt.read_bytes()
        with self.assertRaises(FileExistsError):
            main(['--candidate',str(self.pkg),'--bundle',str(frozen),'--expected-sha256',digest(frozen.read_bytes()),'--receipt',str(receipt)])
        self.assertEqual(before,receipt.read_bytes())
