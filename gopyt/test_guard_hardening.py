"""Guard boundary regressions: typed cases, policy parsing and bounded execution."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from gopyt.guard import canonical,digest,evaluate,prepare_bundle,timed_call,main
from gopyt.testing import write_pkg

class GuardHardening(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'policy';self.root.mkdir()
        declarations=[('integer','value: i64','i64','0'),('boolean','value: bool','bool','true'),('text','value: str','i64','0')]
        spec='module cases\n\n';impl=spec
        for name,params,ret,body in declarations:
            sig=f'fn {name}({params}) -> {ret}\n'
            spec+=sig+'\n';impl+=sig+'{\n    return '+body+'\n}\n\n'
        write_pkg(str(self.root),{'spec/cases.gopyt':spec,'impl/cases.gopyt':impl},name='cases',fmt=True)
        (self.root/'.gopyt-transaction.lock').unlink(missing_ok=True)
        self.good={'symbol':'cases.integer','args':[7],'expected':0}
        self.raw=prepare_bundle(self.root,['impl/cases.gopyt'],[self.good],{'case':'Typed primitive acceptance.'})

    def altered(self,case):
        data=json.loads(self.raw);data['cases'].append(case);return canonical(data)

    def refused(self,case):
        raw=self.altered(case);result=evaluate(raw,digest(raw),self.root)
        self.assertFalse(result['accepted'],result)
        self.assertIn('acceptance signature',result.get('detail','')+result.get('error',''))

    def test_bool_is_not_integer_argument(self):
        self.refused({'symbol':'cases.integer','args':[True],'expected':0})

    def test_integer_is_not_bool_argument(self):
        self.refused({'symbol':'cases.boolean','args':[1],'expected':True})

    def test_unsupported_string_parameter(self):
        self.refused({'symbol':'cases.text','args':[7],'expected':0})

    def test_missing_argument_cannot_match_expected_type_trap(self):
        self.refused({'symbol':'cases.integer','args':[],'trap':9})

    def test_extra_argument_cannot_match_expected_type_trap(self):
        self.refused({'symbol':'cases.integer','args':[7,8],'trap':9})

    def test_prepare_rejects_wrong_result_type(self):
        with self.assertRaisesRegex(ValueError,'acceptance signature'):
            prepare_bundle(self.root,['impl/cases.gopyt'],[{'symbol':'cases.integer','args':[7],'expected':False}],{'case':'Typed expected result.'})

    def test_prepare_rejects_unknown_function(self):
        with self.assertRaisesRegex(ValueError,'acceptance signature'):
            prepare_bundle(self.root,['impl/cases.gopyt'],[{'symbol':'cases.missing','args':[7],'expected':0}],{'case':'Resolved function.'})

    def test_prepare_rejects_wrong_arity(self):
        with self.assertRaisesRegex(ValueError,'acceptance signature'):
            prepare_bundle(self.root,['impl/cases.gopyt'],[self.good,{'symbol':'cases.integer','args':[],'trap':9}],{'case':'Well-typed calls.'})

    def test_prepare_duplicate_policy_key_is_refused(self):
        policy=Path(self.temp.name)/'review.json';out=Path(self.temp.name)/'bundle.json'
        payload='{"mutable":["impl/cases.gopyt"],"cases":[],"cases":'+json.dumps([self.good])+',"obligations":{"case":"Typed inputs."}}'
        policy.write_text(payload)
        self.assertEqual(main(['prepare','--candidate',str(self.root),'--policy',str(policy),'--out',str(out)]),1)
        self.assertFalse(out.exists())

    def test_valid_bool_and_integer_cases_pass(self):
        raw=self.altered({'symbol':'cases.boolean','args':[False],'expected':True})
        self.assertTrue(evaluate(raw,digest(raw),self.root)['accepted'])

    def test_timed_call_preserves_input_list(self):
        class VM:
            cancels=()
            def call(self,fn,args):return args.pop()
        args=[3];self.assertEqual(timed_call(VM(),0,args),3);self.assertEqual(args,[3])

    def test_timeout_and_cancellation_restoration(self):
        import time
        class VM:
            cancels=()
            def call(self,fn,args):
                while not any(c.is_set() for c in self.cancels):pass
                raise TimeoutError('deadline')
        vm=VM();started=time.monotonic()
        with self.assertRaises(TimeoutError):timed_call(vm,0,[],timeout=0.01)
        self.assertLess(time.monotonic()-started,1);self.assertEqual(vm.cancels,())
