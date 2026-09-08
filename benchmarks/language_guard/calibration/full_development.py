"""Broader acceptance policy, all distinct development inputs, no eval additions."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time


def main(a):
    prior=a.prior.resolve();transfer=a.transfer.resolve();out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
    sys.path.insert(0,str(prior/'toolchain'))
    from gopyt.guard import prepare_bundle,evaluate,digest,materialize
    from harness.tasks import load_task
    from harness.verifier import run_declarative_verifier,RunnerOutcome
    rows=json.loads((a.data/'pairs.json').read_bytes())
    cases=[{'symbol':'pricing.extend','args':r['args'],'expected':r['expected']} for r in rows if r['development']]
    raw=prepare_bundle(prior/'baselines'/'weak',['impl/pricing.gopyt'],cases,{'line_extension':'All distinct development inputs; no evaluation inputs selected.'})
    (out/'bundle.json').write_bytes(raw)
    protocol={'cases':len(cases),'bundle_sha256':digest(raw),'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
      'policy':'Every distinct development pair, selected without evaluation-row additions. Post-transfer calibration; not a new sealed evaluation.'}
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    task=load_task('real-scalar-policy',prior/'tasks');results=[]
    paths=[prior/'trials'/'weak-source-clean.json']
    paths += [p for p in sorted((prior/'trials').glob('weak-source-*.json')) if not any(p.name.endswith('-'+n+'.json') for n in ['clean','comment','equivalent_body'])]
    paths += sorted((transfer/'trials').glob('weak-source-*.json'))
    keys=json.loads(raw)['files']
    for report in paths:
        old=json.loads(report.read_bytes());source=report.parent.parent/'candidates'/report.stem
        label=report.parent.parent.name+'-'+old['variant'];root=out/'candidates'/label;root.mkdir(parents=True)
        # Previous oracle builds generated artifacts. Recapture declared source files only.
        materialize({key:(source/key).read_bytes() for key in keys},root)
        observed={};started=time.monotonic()
        def runner(cmd,workspace,timeout):
            if cmd=='guard-admission':
                observed['guard']=evaluate(raw,digest(raw),workspace)
                return RunnerOutcome(0 if observed['guard']['accepted'] else 1)
            if cmd!='evaluation-oracle':raise ValueError(cmd)
            # Retained oracle applies only if the exact source semantics are unchanged.
            assert all((workspace/key).read_bytes()==(source/key).read_bytes() for key in keys if key.endswith('.gopyt'))
            observed['oracle']=old['oracle'];observed['oracle_reused_from']=str(report)
            return RunnerOutcome(0 if old['oracle']['bad_source_rows']==0 else 1)
        score=run_declarative_verifier(task,root,runner=runner)
        result={'variant':old['variant'],'vulcan_verifier':score,'seconds':round(time.monotonic()-started,4),**observed}
        (out/(label+'.json')).write_text(json.dumps(result,indent=2)+'\n');results.append(result)
        print(label,'admitted',observed['guard']['accepted'],'seconds',result['seconds'],flush=True)
        if old['variant']=='clean':assert observed['guard']['accepted'],result
    active=[r for r in results if r['oracle']['bad_source_rows']]
    summary={'completed':True,'trials':len(results),'cases_per_trial':len(cases),
      'evaluation_active_mutants':len(active),'rejected_evaluation_active_mutants':sum(not r['guard']['accepted'] for r in active),
      'accepted_faults':[r['variant'] for r in active if r['guard']['accepted']],
      'guard_case_executions':sum(r['guard'].get('total',0) for r in results),
      'total_seconds':sum(r['seconds'] for r in results),
      'limits':'Same frozen mutations; post-transfer policy calibration, not independent validation of a general detection rate. Evaluation oracles reused only for byte-identical source.'}
    (out/'result.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--prior',type=Path,required=True);p.add_argument('--transfer',type=Path,required=True);p.add_argument('--data',type=Path,required=True);p.add_argument('--out',type=Path,required=True);main(p.parse_args())
