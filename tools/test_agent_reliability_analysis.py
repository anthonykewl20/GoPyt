"""Analysis regressions: incomplete attempts and tampered records stay visible."""
import json
from pathlib import Path
import tempfile
import unittest

from tools.check_agent_reliability_evidence import check, sha
from tools.summarize_agent_reliability import summarize


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + '\n')


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.schedule = [dict(id=f'{task.lower()}-{language}-r{repeat}', task=task,
                              language=language, repeat=repeat, block=0)
                         for task in 'ABC' for language in ('gopyt', 'python', 'typescript', 'go')
                         for repeat in (1, 2)]
        dump(self.root/'manifest.json', dict(schedule=self.schedule,
             configs={k: {} for k in ('gopyt', 'python', 'typescript', 'go')}, frozen_files={}))
        for trial in self.schedule:
            dump(self.root/'trials'/trial['id']/'result.json',
                 dict(trial=trial, primary_mechanical_pass=False, session=None,
                      evaluation=None, disposition='infrastructure_or_incomplete'))

    def completed(self, index=0):
        trial = self.schedule[index]
        directory = self.root/'trials'/trial['id']
        usage = dict(input_tokens=10, cached_input_tokens=2, output_tokens=4)
        events = [dict(type='thread.started', thread_id=trial['id']),
                  dict(type='item.started', item=dict(type='command_execution', id='item_1')),
                  dict(type='item.completed', item=dict(type='command_execution', id='item_1')),
                  dict(type='turn.completed', usage=usage)]
        (directory/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
        dump(directory/'initial-inventory.json', {})
        dump(directory/'final-inventory.json', {})
        for name in ('work', 'submitted'):
            (directory/name).mkdir()
        business = directory/'evaluation/business'
        business.mkdir(parents=True)
        request = b'{"stock":0,"commands":[]}\n'
        response = b'{"results":[]}\n'
        for name, data in [('requests', request), ('expected', response), ('actual', response)]:
            (business/(name+'.jsonl')).write_bytes(data)
        result = dict(trial=trial, primary_mechanical_pass=True,
            source_acceptance=True, session_completion=True, source_snapshots_preserved=True,
            integrity=dict(changed=[],unauthorized=[], symlinks=[]),
            session=dict(exit_code=0,observed_tool_items=1, usage=usage, terminal_event='turn.completed',
                         usage_events=[dict(type='turn.completed', usage=usage)],
                         wall_seconds=2, censored=False),
            evaluation=dict(passed=True,source_unchanged=True,checks={'exit_code':0}, behavior=dict(corpus_sha256=sha(request),
                expected_sha256=sha(response), output_sha256=sha(response),
                scenarios_total=1, scenarios_passed=1, commands_total=0, commands_passed=0,
                passed=True, exit_code=0, failures=[])))
        if trial['task']=='C':
            result['handoff']={'structure_pass':True,'artifact':{'status':'needs_clarification','claims':[],'limitations':[],'questions':['Specify missing policy.']}}
        dump(directory/'result.json', result)
        return trial, directory, result

    def review(self, trial, **extra):
        value = dict(trial_id=trial['id'], source_integrity='pass', verification_claims=[])
        value.update(extra)
        dump(self.root/'reviews'/('trial-'+trial['id']+'.json'), value)

    def test_infrastructure_attempts_retained_and_unknown_usage(self):
        report = summarize(self.root)
        self.assertEqual(report['scheduled'], 24)
        self.assertEqual(report['reviewed'], 0)
        self.assertTrue(all(r['accepted'] is None and r['input_tokens'] is None for r in report['rows']))

    def test_pending_review_is_not_acceptance(self):
        trial, _, _ = self.completed()
        self.assertIsNone(summarize(self.root)['rows'][0]['accepted'])
        self.review(trial)
        self.assertTrue(summarize(self.root)['rows'][0]['accepted'])

    def test_clarification_requires_all_dimensions(self):
        trial, _, _ = self.completed(16)
        clarity = dict(eligibility='yes', benefit='yes', interaction='unknown', safe_deferral='yes')
        self.review(trial, clarification=clarity)
        self.assertFalse(summarize(self.root)['rows'][16]['accepted'])
        clarity['interaction'] = 'yes'
        self.review(trial, clarification=clarity)
        self.assertTrue(summarize(self.root)['rows'][16]['accepted'])

    def test_censored_source_success_is_separate_from_completion(self):
        trial, directory, result = self.completed()
        result['session_completion'] = False
        result['session']['censored'] = True
        dump(directory/'result.json', result)
        self.review(trial)
        row = summarize(self.root)['rows'][0]
        self.assertTrue(row['accepted'])
        self.assertFalse(row['session_completion'])

    def test_raw_records_reconcile_and_deduplicate_item_events(self):
        self.completed()
        self.assertEqual(check(self.root, False)['errors'], [])

    def test_output_tampering_and_counter_inflation_detected(self):
        _, directory, result = self.completed()
        (directory/'evaluation/business/actual.jsonl').write_text('{"results":[],"fake":true}\n')
        result['evaluation']['behavior']['scenarios_passed'] = 20
        dump(directory/'result.json', result)
        errors = check(self.root, False)['errors']
        self.assertTrue(any('actual hash' in e for e in errors))
        self.assertTrue(any('passed scenario count' in e for e in errors))

    def test_usage_and_source_tampering_detected(self):
        _, directory, result = self.completed()
        result['session']['usage'] = dict(input_tokens=1)
        dump(directory/'result.json', result)
        (directory/'submitted/new.py').write_text('pass\n')
        errors = check(self.root, False)['errors']
        self.assertTrue(any('final usage differs' in e for e in errors))
        self.assertTrue(any('submitted source changed' in e for e in errors))

    def test_reviews_and_frozen_hash_required(self):
        trial, _, _ = self.completed()
        self.assertTrue(any('pending independent review' in e for e in check(self.root)['errors']))
        self.review(trial)
        manifest = json.loads((self.root/'manifest.json').read_text())
        manifest['frozen_files'] = {'missing.txt': sha(b'missing')}
        dump(self.root/'manifest.json', manifest)
        self.assertTrue(any('frozen hash' in e for e in check(self.root)['errors']))

    def test_primary_flag_cannot_override_missing_or_failed_source_acceptance(self):
        for mode in ('missing-evaluation','failed-source','failed-static','changed-source'):
            with self.subTest(mode=mode):
                trial,directory,result=self.completed()
                if mode=='missing-evaluation':result['evaluation']=None
                elif mode=='failed-source':result['source_acceptance']=False
                elif mode=='failed-static':result['evaluation']['checks']['exit_code']=1
                else:result['evaluation']['source_unchanged']=False
                dump(directory/'result.json',result);self.review(trial)
                self.assertFalse(summarize(self.root)['rows'][0]['accepted'])
                self.assertTrue(any('inconsistent' in e for e in check(self.root,False)['errors']))
                import shutil
                shutil.rmtree(directory)
                directory.mkdir()

    def test_c_primary_requires_structured_clarification_even_if_review_is_yes(self):
        trial,directory,result=self.completed(16)
        result['handoff']['artifact']['status']='implemented'
        dump(directory/'result.json',result)
        self.review(trial,clarification={k:'yes' for k in ('eligibility','benefit','interaction','safe_deferral')})
        self.assertFalse(summarize(self.root)['rows'][16]['accepted'])
        self.assertTrue(any('primary mechanical' in e for e in check(self.root,False)['errors']))

    def test_unauthorized_edits_cannot_be_hidden_in_matching_final_inventories(self):
        _,directory,result=self.completed()
        for folder in ('work','submitted'):
            (directory/folder/'fixed-check.py').write_text('tampered')
        dump(directory/'final-inventory.json',{'fixed-check.py':sha(b'tampered')})
        errors=check(self.root,False)['errors']
        self.assertTrue(any('changed-file inventory differs' in e for e in errors))
        self.assertTrue(any('unauthorized-file inventory differs' in e for e in errors))

    def test_final_newline_and_session_completion_are_reconciled(self):
        _,directory,result=self.completed()
        payload=b'{"results":[]}'
        (directory/'evaluation/business/actual.jsonl').write_bytes(payload)
        result['evaluation']['behavior']['output_sha256']=sha(payload)
        result['session']['censored']=True
        dump(directory/'result.json',result)
        errors=check(self.root,False)['errors']
        self.assertTrue(any('business pass' in e for e in errors))
        self.assertTrue(any('session completion' in e for e in errors))

    def test_failed_attempts_also_require_review(self):
        errors = check(self.root)['errors']
        self.assertEqual(sum('pending independent review' in e for e in errors), 24)


if __name__ == '__main__':
    unittest.main()
