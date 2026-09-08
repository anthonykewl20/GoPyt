"""Frozen-selector transfer check: new predicates, no acceptance-case retuning."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

PREDICATES=[('six_units','quantity == 6'),('price_255','price == 255'),
 ('medium_order','quantity >= 50 and quantity <= 60'),
 ('price_band','price >= 300 and price <= 400'),
 ('dozen','quantity % 12 == 0'),('price_499','price == 499'),
 ('large_expensive_return','quantity < -10 and price > 5000')]

def save(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:json.dump(obj,f,indent=2,sort_keys=True);f.write('\n')

def main(a):
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
    prior=a.prior.resolve();sys.path.insert(0,str(prior/'toolchain'))
    from gopyt.guard import evaluate,digest,compile_policy
    from gopyt.vm import Trap
    from harness.tasks import load_task,task_hash
    from harness.verifier import run_declarative_verifier,RunnerOutcome
    raw=(a.data/'pairs.json').read_bytes();records=json.loads(raw);evaluation=[r for r in records if r['evaluation']]
    task=load_task('real-scalar-policy',prior/'tasks')
    save(out/'protocol.json',{'predicates':PREDICATES,'selector_policy':'Reuse prior bundle bytes exactly; no selection changes.',
       'data_sha256':hashlib.sha256(raw).hexdigest(),'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
       'vulcan_task_hash':task_hash(task),'note':'New constructed predicates after first campaign; not independently authored or a sealed holdout.'})
    results=[]
    for lane in ['weak','exact']:
        for mode in ['source','hash','coverage'] if lane=='weak' else ['coverage']:
            bundle=(prior/'bundles'/f'{lane}-{mode}.json').read_bytes()
            for name,expr in PREDICATES:
                root=out/'candidates'/f'{lane}-{mode}-{name}';shutil.copytree(prior/'baselines'/lane,root)
                f=root/'impl/pricing.gopyt';f.write_text(f.read_text().replace('    return multiply(quantity, price)',
                    f'    if {expr} {{\n        return quantity * price + 1\n    }}\n    return multiply(quantity, price)'))
                observed={}
                def runner(cmd,workspace,timeout):
                    if cmd=='guard-admission':
                        r=evaluate(bundle,digest(bundle),workspace);observed['guard']=r
                        return RunnerOutcome(0 if r['accepted'] else 1)
                    if cmd!='evaluation-oracle':raise ValueError(cmd)
                    vm,ids=compile_policy(workspace);wrong=0;traps=0;bad_rows=0;examples=[]
                    for r in evaluation:
                        try:
                            value=vm.call(ids['pricing.extend'],list(r['args']));bad=type(value) is not int or value!=r['expected'];wrong+=bad;detail={'actual':value}
                        except Trap as exc:
                            bad=True;traps+=1;detail={'trap':exc.code}
                        if bad:
                            bad_rows+=r['evaluation']
                            if len(examples)<3:examples.append({'args':r['args'],'expected':r['expected'],**detail,'source':r['source_rows']['evaluation']})
                    observed['oracle']={'wrong_return_pairs':wrong,'trapping_pairs':traps,'bad_source_rows':bad_rows,'executed_pairs':len(evaluation),'examples':examples}
                    return RunnerOutcome(0 if wrong+traps==0 else 1)
                score=run_declarative_verifier(task,root,runner=runner)
                result={'lane':lane,'selector':mode,'variant':name,'vulcan_verifier':score,**observed}
                save(out/'trials'/f'{lane}-{mode}-{name}.json',result);results.append(result)
                print(lane,mode,name,'admitted',observed['guard']['accepted'],'wrong',observed['oracle']['wrong_return_pairs'],'traps',observed['oracle']['trapping_pairs'],flush=True)
    summary={}
    for lane,mode in [('weak','source'),('weak','hash'),('weak','coverage'),('exact','coverage')]:
        group=[r for r in results if r['lane']==lane and r['selector']==mode]
        active=[r for r in group if r['oracle']['bad_source_rows']]
        summary[lane+'-'+mode]={'mutants':len(group),'evaluation_active':len(active),
           'rejected':sum(not r['guard']['accepted'] for r in group),
           'admitted_wrong_return_pairs':sum(r['oracle']['wrong_return_pairs'] for r in group if r['guard']['accepted']),
           'admitted_trapping_pairs':sum(r['oracle']['trapping_pairs'] for r in group if r['guard']['accepted'])}
    assert all(r['oracle']['wrong_return_pairs']==0 for r in results if r['lane']=='exact')
    save(out/'result.json',{'completed':True,'trials':len(results),'summary':summary,'evaluation_vm_executions':sum(r['oracle']['executed_pairs'] for r in results),
       'limits':'Sums over mutants overlap input pairs. Zero wrong returns under exact contract does not imply all valid calls complete.'})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--prior',type=Path,required=True);p.add_argument('--data',type=Path,required=True);p.add_argument('--out',type=Path,required=True);main(p.parse_args())
