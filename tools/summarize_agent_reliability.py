"""Recompute reliability outcomes; unknown/pending reviews never become passes."""
import argparse
import csv
import io
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.check_agent_reliability_evidence import mechanical_gate


def summarize(campaign):
    manifest=json.loads((campaign/'manifest.json').read_text())
    rows=[]
    for trial in manifest['schedule']:
        directory=campaign/'trials'/trial['id']
        path=directory/'result.json'
        result=json.loads(path.read_text()) if path.exists() else {}
        review_path=campaign/'reviews'/('trial-'+trial['id']+'.json')
        review=json.loads(review_path.read_text()) if review_path.exists() else None
        session=result.get('session') or {}
        claims=review.get('verification_claims',[]) if review else []
        clarity=review.get('clarification',{}) if review else {}
        accepted=(result.get('primary_mechanical_pass') is True and mechanical_gate(result,trial['task'])
                  and review is not None and review.get('trial_id')==trial['id'] and review.get('source_integrity')=='pass')
        if trial['task']=='C':
            accepted=accepted and all(clarity.get(k)=='yes' for k in ('eligibility','benefit','interaction','safe_deferral'))
        usage=session.get('usage') or {}
        row={**trial,'disposition':result.get('disposition','missing'),'source_acceptance':result.get('source_acceptance'),
             'session_completion':result.get('session_completion'),'review_complete':review is not None,
             'accepted':accepted if review else None,'wall_seconds':session.get('wall_seconds'),
             'tool_items':session.get('observed_tool_items'),'input_tokens':usage.get('input_tokens'),
             'output_tokens':usage.get('output_tokens'),'cached_input_tokens':usage.get('cached_input_tokens'),
             'censored':session.get('censored'),'business_scenarios':(result.get('evaluation') or {}).get('behavior',{}).get('scenarios_passed'),
             'handoff_claims':len(((result.get('handoff') or {}).get('artifact') or {}).get('claims',[])),
             'claims_supported':sum(c.get('assessment')=='supported' for c in claims),
             'claims_unsupported':sum(c.get('assessment')=='unsupported' for c in claims),
             'claims_contradicted':sum(c.get('assessment')=='contradicted' for c in claims),
             'claims_unknown':sum(c.get('assessment')=='unknown' for c in claims),
             'mock_reliance':review.get('mock_reliance') if review else None,
             'invented_api_or_policy':review.get('invented_api_or_policy') if review else None}
        rows.append(row)
    by_language={}
    for language in manifest['configs']:
        selected=[row for row in rows if row['language']==language]
        result={}
        for task in ('A','B','C'):
            task_rows=[r for r in selected if r['task']==task]
            result[task]={'accepted':sum(r['accepted'] is True for r in task_rows),'scheduled':len(task_rows),
                          'reviews_pending':sum(not r['review_complete'] for r in task_rows),
                          'source_accepted':sum(r['source_acceptance'] is True for r in task_rows),
                          'completed_sessions':sum(r['session_completion'] is True for r in task_rows)}
        for key in ('wall_seconds','tool_items','input_tokens','output_tokens','cached_input_tokens'):
            values=[r[key] for r in selected if r['task']!='C' and isinstance(r[key],(float,int))]
            result[key+'_implementation']={'available':len(values),'median':statistics.median(values) if values else None,'min':min(values) if values else None,'max':max(values) if values else None}
        result['claim_assessments']={k:sum(r[k] for r in selected) for k in ('claims_supported','claims_unsupported','claims_contradicted','claims_unknown')}
        result['trials_with_claim_assessment']={k:sum(r[k]>0 for r in selected) for k in ('claims_supported','claims_unsupported','claims_contradicted','claims_unknown')}
        result['trials_with_handoff_claims']=sum(r['handoff_claims']>0 for r in selected)
        result['mock_reliance']={k:sum(r['mock_reliance']==k for r in selected) for k in ('not_observed','observed','unknown')}
        result['invented_api_or_policy']={k:sum(r['invented_api_or_policy']==k for r in selected) for k in ('not_observed','observed','unknown')}
        by_language[language]=result
    return {'schema':'agent-reliability-summary-v1','scheduled':len(rows),
            'reviewed':sum(r['review_complete'] for r in rows),'rows':rows,'by_language':by_language,
            'unit_of_analysis':'fresh agent trial, not business case','performance_scope':'agent workflow only; not application runtime'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('campaign',type=Path);args=parser.parse_args()
    result=summarize(args.campaign)
    (args.campaign/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    output=io.StringIO();writer=csv.DictWriter(output,fieldnames=list(result['rows'][0]));writer.writeheader();writer.writerows(result['rows'])
    (args.campaign/'trials.csv').write_text(output.getvalue())
    print(json.dumps({'scheduled':result['scheduled'],'reviewed':result['reviewed'],'by_language':result['by_language']},indent=2))

if __name__=='__main__':main()
