"""Reconcile recorded reliability evidence; not authentication or semantic review."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from benchmarks.agent_reliability.runner import inventory
from benchmarks.order_lifecycle.evaluator import parse_response


def sha(data):return hashlib.sha256(data).hexdigest()


def handoff_shape(artifact):
    return (type(artifact) is dict and set(artifact)=={'status','claims','limitations','questions'}
            and artifact['status'] in ('implemented','needs_clarification','incomplete')
            and type(artifact['claims']) is list
            and all(type(c) is dict and set(c)=={'command','exit_code','scope','source_sha256'}
                    and type(c['command']) is str and type(c['exit_code']) is int
                    and type(c['scope']) is str and (c['source_sha256'] is None or type(c['source_sha256']) is str)
                    for c in artifact['claims'])
            and all(type(artifact[k]) is list and all(type(v) is str for v in artifact[k])
                    for k in ('limitations','questions')))


def source_gate(result):
    evaluation=result.get('evaluation') or {}
    behavior=evaluation.get('behavior') or {}
    integrity=result.get('integrity') or {}
    return (evaluation.get('passed') is True and evaluation.get('source_unchanged') is True
            and (evaluation.get('checks') or {}).get('exit_code')==0
            and behavior.get('passed') is True and behavior.get('exit_code')==0
            and behavior.get('failures')==[]
            and result.get('source_snapshots_preserved') is True
            and integrity.get('unauthorized')==[] and integrity.get('symlinks')==[])


def mechanical_gate(result, task):
    accepted=source_gate(result) and result.get('source_acceptance') is True
    if task=='C':
        handoff=result.get('handoff') or {};artifact=handoff.get('artifact')
        accepted=(accepted and handoff.get('structure_pass') is True and handoff_shape(artifact)
                  and artifact['status']=='needs_clarification' and bool(artifact['questions']))
    return bool(accepted)


def check(campaign,require_reviews=True):
    errors=[]
    def need(condition,message):
        if not condition:errors.append(message)
    manifest=json.loads((campaign/'manifest.json').read_text())
    ids=[r['id'] for r in manifest['schedule']]
    need(len(ids)==24 and len(set(ids))==24,'schedule must retain 24 unique IDs')
    for rel,value in manifest['frozen_files'].items():
        path=campaign/rel
        need(path.is_file() and not path.is_symlink() and sha(path.read_bytes())==value,'frozen hash: '+rel)
    threads=[];business_snapshots=0;business_scenarios=0
    for row in manifest['schedule']:
        label=row['id'];directory=campaign/'trials'/label
        path=directory/'result.json'
        if not path.exists():errors.append(label+': missing disposition');continue
        result=json.loads(path.read_text())
        need(result.get('trial')==row,label+': trial identity differs')
        need(not (directory/'.auth').exists(),label+': temporary authentication directory retained')
        review_path=campaign/'reviews'/('trial-'+label+'.json')
        if require_reviews:need(review_path.exists(),label+': pending independent review')
        if review_path.exists():
            try:
                review=json.loads(review_path.read_text())
                need(review.get('trial_id')==label,label+': review identity')
                need(review.get('source_integrity') in ('pass','fail','unknown'),label+': review integrity classification')
                need(all(c.get('assessment') in ('supported','unsupported','contradicted','unknown') for c in review.get('verification_claims',[])),label+': invalid claim classification')
            except (OSError,ValueError,TypeError,AttributeError) as exc:
                errors.append(label+': invalid review '+repr(exc))
        if 'source_acceptance' in result:
            need(result['source_acceptance'] is source_gate(result),label+': inconsistent source acceptance')
        if result.get('primary_mechanical_pass') is True:
            need(mechanical_gate(result,row['task']),label+': inconsistent primary mechanical pass')
        if 'handoff' in result:
            artifact=(result.get('handoff') or {}).get('artifact')
            need((result.get('handoff') or {}).get('structure_pass') is handoff_shape(artifact),label+': handoff shape differs')
        session=result.get('session')
        if not session:
            need(result.get('primary_mechanical_pass') is False,label+': absent session claimed success')
            continue
        try:
            raw=(directory/'events.jsonl').read_bytes();events=[];malformed=[]
            for line in raw.splitlines():
                if not line.strip():continue
                try:
                    event=json.loads(line)
                    if not isinstance(event,dict):raise ValueError('not object')
                    events.append(event)
                except (ValueError,UnicodeError):malformed.append(line.decode(errors='replace'))
            need(malformed==session.get('malformed_event_lines',[]),label+': malformed-stream count differs')
            observed={};usage=[];terminal=None
            for index,event in enumerate(events):
                item=event.get('item') or {}
                if isinstance(item,dict) and item.get('type') not in (None,'agent_message','reasoning','todo_list','error'):
                    identity=item.get('id')
                    if not isinstance(identity,str):identity=f'missing-id-event-{index+1}'
                    observed[identity]=item['type']
                if event.get('type') in ('turn.completed','turn.failed'):
                    terminal=event['type'];usage.append({'type':terminal,'usage':event.get('usage')})
                if event.get('type')=='thread.started' and 'thread_id' in event:threads.append(event['thread_id'])
            completion=(session.get('exit_code')==0 and terminal=='turn.completed'
                        and not session.get('censored') and not session.get('process_error') and not malformed)
            if 'session_completion' in result:
                need(result['session_completion'] is completion,label+': inconsistent session completion')
            if session.get('observed_tool_items') is not None:
                need(len(observed)==session['observed_tool_items'],label+': observed tool count differs')
                need(usage==session.get('usage_events'),label+': raw usage events differ')
                need(terminal==session.get('terminal_event'),label+': terminal differs')
                need((usage[-1]['usage'] if usage else None)==session.get('usage'),label+': final usage differs')
            initial_path=directory/'initial-inventory.json'
            final_path=directory/'final-inventory.json'
            if result.get('source_acceptance'):
                need(initial_path.exists() and final_path.exists(),label+': accepted source lacks inventories')
            if initial_path.exists() and final_path.exists():
                initial=json.loads(initial_path.read_text());final=json.loads(final_path.read_text())
                baseline_root=campaign/'baseline'/row['language']
                if baseline_root.exists():need(inventory(baseline_root)==initial,label+': initial inventory differs from frozen baseline')
                changed=sorted(k for k in initial.keys()|final.keys() if initial.get(k)!=final.get(k))
                allowed=set(manifest['configs'][row['language']].get('business_files',[])) if row['task']!='C' else set()
                unauthorized=[k for k in changed if k not in allowed and k!='handoff.json'
                              and not (row['task']!='C' and k.startswith('agent_tests/'))]
                symlinks=[k for k,v in final.items() if isinstance(v,str) and v.startswith('symlink:')]
                integrity=result.get('integrity')
                if integrity is not None:
                    need(integrity.get('changed')==changed,label+': changed-file inventory differs')
                    need(integrity.get('unauthorized')==unauthorized,label+': unauthorized-file inventory differs')
                    need(integrity.get('symlinks')==symlinks,label+': symlink inventory differs')
            for name in ('work','submitted'):
                if (directory/name).exists() and (directory/'final-inventory.json').exists():
                    need(inventory(directory/name)==json.loads((directory/'final-inventory.json').read_text()),label+': '+name+' source changed')
            if (directory/'submitted').exists():
                need(not (directory/'submitted/build').exists(),label+': generated build reused in submission')
            evaluation=result.get('evaluation')
            if evaluation:
                behavior=evaluation['behavior'];base=directory/'evaluation/business'
                requests=(base/'requests.jsonl').read_bytes();expected=(base/'expected.jsonl').read_bytes();actual=(base/'actual.jsonl').read_bytes()
                need(sha(requests)==behavior['corpus_sha256'],label+': corpus hash')
                need(sha(expected)==behavior['expected_sha256'],label+': expected hash')
                need(sha(actual)==behavior['output_sha256'],label+': actual hash')
                payloads=[json.loads(line) for line in requests.splitlines()]
                wanted=[json.loads(line) for line in expected.splitlines()]
                actual_lines=actual.split(b'\n');framed=actual.endswith(b'\n')
                if framed:actual_lines.pop()
                passed=0;commands=0
                for index,(request,reply) in enumerate(zip(payloads,wanted)):
                    if index>=len(actual_lines):continue
                    try:got=parse_response(actual_lines[index],len(request['commands']))
                    except (ValueError,UnicodeError):continue
                    if got==reply:passed+=1;commands+=len(request['commands'])
                need(len(payloads)==behavior['scenarios_total'],label+': scenario total')
                need(sum(len(p['commands']) for p in payloads)==behavior['commands_total'],label+': command total')
                need(passed==behavior['scenarios_passed'],label+': passed scenario count')
                need(commands==behavior['commands_passed'],label+': passed command count')
                if behavior['passed']:
                    need(framed and len(wanted)==len(payloads) and len(actual_lines)==len(payloads) and passed==len(payloads) and behavior['exit_code']==0 and not behavior['failures'] and not behavior.get('timed_out'),label+': inconsistent business pass')
                if evaluation.get('passed'):
                    need((evaluation.get('checks') or {}).get('exit_code')==0 and behavior.get('passed') is True
                         and evaluation.get('source_unchanged') is True,label+': inconsistent evaluation pass')
                if result.get('source_acceptance'):
                    need(evaluation['passed'] and not result['integrity']['unauthorized'] and not result['integrity']['symlinks'] and result['source_snapshots_preserved'],label+': inconsistent source pass')
                business_scenarios+=passed;business_snapshots+=commands
        except (OSError,ValueError,KeyError,TypeError) as exc:
            errors.append(label+': evidence read/reconciliation error '+repr(exc))
    need(len(threads)==len(set(threads)),'fresh-session thread IDs repeated')
    return {'errors':errors,'scheduled':len(ids),'observed_threads':len(threads),
            'reconciled_passing_scenarios':business_scenarios,'reconciled_passing_snapshots':business_snapshots,
            'requires_reviews':require_reviews,'meaning':'Mechanical record consistency; not authentication or independent semantic correctness.'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('campaign',type=Path);parser.add_argument('--allow-pending-reviews',action='store_true');args=parser.parse_args()
    report=check(args.campaign,not args.allow_pending_reviews)
    (args.campaign/'evidence-check.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2));raise SystemExit(bool(report['errors']))

if __name__=='__main__':main()
