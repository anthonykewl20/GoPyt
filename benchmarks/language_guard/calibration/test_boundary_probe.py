import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from benchmarks.language_guard.calibration.boundary_probe import probe, vectors
from gopyt.guard import prepare_bundle, digest
from gopyt.testing import write_pkg

ENGINE = Path(__file__).resolve().parents[3]
SCRIPT = Path(__file__).with_name('boundary_probe.py')


class BoundaryProbe(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.base = self.root/'base'; self.base.mkdir()
        sig = 'fn reserve(stock: i64, count: i64) -> i64\n'
        write_pkg(str(self.base), {'spec/policy.gopyt': 'module policy\n\n'+sig,
                  'impl/policy.gopyt': 'module policy\n\n'+sig+'{\n    return stock - count\n}\n'}, name='policy', fmt=True)
        (self.base/'.gopyt-transaction.lock').unlink(missing_ok=True)
        self.raw = prepare_bundle(self.base, ['impl/policy.gopyt'],
                                 [{'symbol':'policy.reserve','args':[10,3],'expected':7}],
                                 {'subtract':'Preserve baseline subtraction.'})
        self.candidate = self.root/'candidate'; shutil.copytree(self.base, self.candidate)

    def run_probe(self, **kw):
        return probe(ENGINE, self.base, self.candidate, self.raw, digest(self.raw), **kw)

    def mutate(self):
        p = self.candidate/'impl/policy.gopyt'
        p.write_text(p.read_text().replace('    return stock - count',
                                          '    if stock == 42 {\n        return 99\n    }\n    return stock - count'))

    def test_clean_and_equivalent_candidates_have_no_differences(self):
        self.assertEqual(self.run_probe()['mismatches'], 0)
        p=self.candidate/'impl/policy.gopyt'; p.write_text(p.read_text().replace('stock - count', 'stock + -count'))
        self.assertEqual(self.run_probe()['mismatches'], 0)

    def test_finds_rare_branch_that_approved_case_misses(self):
        self.mutate(); report=self.run_probe()
        self.assertEqual(report['candidate_acceptance_passed'], 1)
        self.assertGreater(report['mismatches'], 0)
        self.assertTrue(any(c['args']==[42,3] for c in report['counterexamples']))

    def test_joint_condition_is_detected(self):
        p=self.candidate/'impl/policy.gopyt'
        p.write_text(p.read_text().replace('    return stock - count',
                     '    if stock == 42 and count == 17 {\n        return 99\n    }\n    return stock - count'))
        report=self.run_probe()
        self.assertGreater(report['mismatches'],0)
        self.assertTrue(any(c['args']==[42,17] for c in report['counterexamples']))

    def test_computed_affine_reversed_and_nested_conditions(self):
        p=self.candidate/'impl/policy.gopyt';original=p.read_text()
        for condition in ['stock == 6 * 7 and count == 8 + 9',
                          'stock + 7 == 49 and count * 3 == 51',
                          '49 == stock + 7 and 51 == 3 * count',
                          'stock == -42 and count == -17']:
            with self.subTest(condition=condition):
                p.write_text(original.replace('    return stock - count',
                    '    if '+condition+' {\n        return 99\n    }\n    return stock - count'))
                self.assertGreater(self.run_probe()['mismatches'],0)
        p.write_text(original.replace('    return stock - count',
                    '    if stock == 42 {\n        if count == 17 {\n            return 99\n        }\n    }\n    return stock - count'))
        self.assertGreater(self.run_probe()['mismatches'],0)

    def test_finite_domain_closes_nonlinear_heuristic_miss(self):
        p=self.candidate/'impl/policy.gopyt'
        p.write_text(p.read_text().replace('    return stock - count',
            '    if stock > 0 and stock < 100 and count > 0 and count < 100 {\n        if stock * count == 714 {\n            return 99\n        }\n    }\n    return stock - count'))
        self.assertEqual(self.run_probe()['mismatches'],0)
        domains={'policy.reserve':[list(range(40,45)),list(range(15,20))]}
        report=self.run_probe(domains=domains)
        self.assertTrue(report['domain_fully_compared'])
        self.assertEqual(report['generated_executed'],25)
        self.assertEqual(report['mismatches'],1)
        self.assertEqual(report['counterexamples'][0]['args'],[42,17])

    def test_finite_domain_refuses_budget_and_type_gaps(self):
        for domains in [{'policy.reserve':[list(range(50)),list(range(50))]},
                        {'policy.reserve':[[True],[3]]},
                        {'policy.reserve':[[],[3]]}, {},
                        {'policy.reserve':[[2**63],[3]]}]:
            with self.subTest(domains=domains):
                with self.assertRaises(ValueError):self.run_probe(domains=domains)

    def test_explicit_tuples_are_complete_and_typed(self):
        self.mutate()
        report=self.run_probe(domains={'policy.reserve':{'tuples':[[42,17],[10,3],[42,17]]}})
        self.assertEqual(report['generated_executed'],2)
        self.assertEqual(report['mismatches'],1)
        self.assertTrue(report['domain_fully_compared'])
        for tuples in [[],[[True,3]],[[1]],[[2**63,0]],'invalid']:
            with self.subTest(tuples=tuples):
                with self.assertRaises(ValueError):
                    self.run_probe(domains={'policy.reserve':{'tuples':tuples}})
        with self.assertRaisesRegex(ValueError,'budget'):
            self.run_probe(limit=1,domains={'policy.reserve':{'tuples':[[42,17],[10,3]]}})

    def test_baseline_traps_are_compared(self):
        p=self.candidate/'impl/policy.gopyt'
        p.write_text(p.read_text().replace('    return stock - count',
            '    if stock < 0 {\n        return 0\n    }\n    return stock - count'))
        report=self.run_probe(domains={'policy.reserve':[[-(2**63)],[1]]})
        self.assertEqual(report['baseline_traps_skipped'],0)
        self.assertEqual(report['baseline_traps_compared'],1)
        self.assertEqual(report['mismatches'],1)
        self.assertIn('trap',report['counterexamples'][0]['baseline'])

    def test_cancelled_call_cannot_claim_complete_domain(self):
        from unittest.mock import patch
        from gopyt.vm import Cancelled
        with patch('gopyt.guard.timed_call',side_effect=[7,7,Cancelled()]):
            with self.assertRaises(Cancelled):
                self.run_probe(domains={'policy.reserve':[[42],[17]]})

    def test_finite_domain_cli_and_duplicate_axis_values(self):
        self.mutate();bundle=self.root/'bundle.json';bundle.write_bytes(self.raw)
        domains=self.root/'domains.json';domains.write_text(json.dumps({'policy.reserve':[[42,42],[17,17]]}))
        out=self.root/'finite.json'
        result=subprocess.run([sys.executable,str(SCRIPT),'--engine',str(ENGINE),
             '--baseline',str(self.base),'--candidate',str(self.candidate),'--bundle',str(bundle),
             '--pin',digest(self.raw),'--domains',str(domains),'--out',str(out)],capture_output=True,text=True,timeout=120)
        self.assertEqual(result.returncode,0,result.stderr)
        report=json.loads(out.read_bytes());self.assertTrue(report['domain_fully_compared'])
        self.assertEqual(report['generated_executed'],1)
        self.assertEqual(report['mismatches'],1)

    def test_three_argument_boolean_conjunction(self):
        base=self.root/'three';base.mkdir()
        sig='fn reserve(stock: i64, count: i64, enabled: bool) -> i64\n'
        write_pkg(str(base),{'spec/policy.gopyt':'module policy\n\n'+sig,
             'impl/policy.gopyt':'module policy\n\n'+sig+'{\n    return stock - count\n}\n'},name='policy',fmt=True)
        (base/'.gopyt-transaction.lock').unlink(missing_ok=True)
        raw=prepare_bundle(base,['impl/policy.gopyt'],[{'symbol':'policy.reserve','args':[10,3,False],'expected':7}],{'rule':'subtract'})
        candidate=self.root/'three-candidate';shutil.copytree(base,candidate)
        p=candidate/'impl/policy.gopyt';p.write_text(p.read_text().replace('    return stock - count',
            '    if stock == 42 and count == 17 and enabled {\n        return 99\n    }\n    return stock - count'))
        report=probe(ENGINE,base,candidate,raw,digest(raw))
        self.assertTrue(any(c['args']==[42,17,True] for c in report['counterexamples']))

    def test_baseline_expected_value_must_actually_pass(self):
        bad=json.loads(self.raw);bad['cases'][0]['expected']=123
        raw=json.dumps(bad).encode()
        with self.assertRaisesRegex(ValueError,'baseline fails'):
            probe(ENGINE,self.base,self.candidate,raw,digest(raw))

    def test_baseline_mutation_is_refused(self):
        p=self.base/'impl/policy.gopyt';p.write_text(p.read_text()+'\n')
        with self.assertRaisesRegex(ValueError, 'every approved file'):self.run_probe()

    def test_bad_pin_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            probe(ENGINE,self.base,self.candidate,self.raw,'0'*64)

    def test_protected_change_is_refused(self):
        p=self.candidate/'spec/policy.gopyt';p.write_text(p.read_text()+'\n')
        with self.assertRaisesRegex(ValueError, 'protected files'):self.run_probe()

    def test_budget_and_boolean_types(self):
        cases=[{'symbol':'policy.flag','args':[True,2],'expected':True}]
        rows=list(vectors(cases,[499],2))
        self.assertEqual(len(rows),2)
        self.assertIs(rows[0][1][0],False)
        self.assertIs(rows[1][1][0],True)
        self.assertEqual(self.run_probe(limit=1)['generated_executed'],1)
        self.assertTrue(self.run_probe(limit=1)['truncated']['probes'])

    def test_cli_runs_isolated_and_refuses_output_overwrite(self):
        self.mutate(); bundle=self.root/'bundle.json';bundle.write_bytes(self.raw);out=self.root/'report.json'
        cmd=[sys.executable,str(SCRIPT),'--engine',str(ENGINE),'--baseline',str(self.base),
             '--candidate',str(self.candidate),'--bundle',str(bundle),'--pin',digest(self.raw),'--out',str(out)]
        first=subprocess.run(cmd,capture_output=True,text=True,timeout=120)
        self.assertEqual(first.returncode,0,first.stderr)
        original=out.read_bytes();self.assertGreater(json.loads(original)['mismatches'],0)
        second=subprocess.run(cmd,capture_output=True,text=True,timeout=120)
        self.assertNotEqual(second.returncode,0);self.assertEqual(out.read_bytes(),original)


if __name__=='__main__':unittest.main()
