"""Real-data calibration through the installed VulcanBench declarative verifier.

Run with VulcanBench's Python. No provider, model score, Docker claim, or new
agent run is implied. Runner dispatch is restricted to our two named operations.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

PREDICATES=[('negative_quantity','quantity < 0',lambda quantity,price:quantity<0),
 ('zero_quantity','quantity == 0',lambda quantity,price:quantity==0),
 ('zero_price','price == 0',lambda quantity,price:price==0),
 ('negative_price','price < 0',lambda quantity,price:price<0),
 ('bulk_quantity','quantity >= 1000',lambda quantity,price:quantity>=1000),
 ('high_price','price >= 100000',lambda quantity,price:price>=100000),
 ('single_quantity','quantity == 1',lambda quantity,price:quantity==1),
 ('one_penny','price == 1',lambda quantity,price:price==1),
 ('large_quantity','quantity > 100',lambda quantity,price:quantity>100),
 ('large_return','quantity < -100',lambda quantity,price:quantity< -100),
 ('fractional_pound','price % 100 != 0',lambda quantity,price:price%100!=0)]

def save(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:json.dump(obj,f,indent=2,sort_keys=True);f.write('\n')

def sha(raw):return hashlib.sha256(raw).hexdigest()

def select(records,mode):
    dev=[r for r in records if r['development']]
    ordered=sorted(dev,key=lambda r:sha(json.dumps(r['args']).encode()))
    if mode=='source':return dev[:128]
    if mode=='hash':return ordered[:128]
    chosen=[]
    for _,_,predicate in PREDICATES:
        matching=next((r for r in ordered if predicate(*r['args'])),None)
        if matching is not None and matching not in chosen:chosen.append(matching)
    for r in ordered:
        if r not in chosen:chosen.append(r)
        if len(chosen)==128:break
    return chosen

def fixture(root,strong):
    root.mkdir(parents=True,exist_ok=False)
    from gopyt.testing import write_pkg
    common='    requires quantity >= -1000000 and quantity <= 1000000\n    requires price >= -100000000 and price <= 100000000\n'
    rule='fn multiply(quantity: i64, price: i64) -> i64\n'
    sig='fn extend(quantity: i64, price: i64) -> i64\n'+common
    sig+=('    ensures result == multiply(quantity, price)\n' if strong else
          '    ensures result >= -100000000000001 and result <= 100000000000001\n')
    write_pkg(str(root),{'spec/pricing.gopyt':'module pricing\n\n'+rule+'\n'+sig,
       'impl/pricing.gopyt':'module pricing\n\n'+rule+'{\n    return quantity * price\n}\n\n'+sig+'{\n    return multiply(quantity, price)\n}\n'},name='pricing',fmt=True)
    (root/'.gopyt-transaction.lock').unlink(missing_ok=True)

def main(a):
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
    # Capture only source, before importing either toolchain. No live Peon changes.
    frozen=out/'toolchain'
    for package,source in [('gopyt',a.gopyt/'gopyt'),('harness',a.vulcan/'harness')]:
        for f in sorted(source.rglob('*.py')):
            if '__pycache__' in f.parts:continue
            target=frozen/package/f.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True)
            raw=f.read_bytes();assert raw==f.read_bytes();target.write_bytes(raw)
    source_hashes={str(f.relative_to(frozen)):sha(f.read_bytes()) for f in frozen.rglob('*.py')}
    sys.path.insert(0,str(frozen))
    from gopyt.guard import prepare_bundle,evaluate,digest,compile_policy,engine_digest
    from gopyt.vm import Trap
    from harness.tasks import load_task,task_hash
    from harness.verifier import run_declarative_verifier,RunnerOutcome
    import subprocess
    revision=subprocess.run(['git','-C',str(a.vulcan),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
    save(out/'toolchain.json',{'sources':source_hashes,'engine_sha256':engine_digest(),
       'vulcan_git_head':revision,'note':'Exact captured source hashes are authoritative for local working-tree code.','python':sys.version})
    raw=(a.data/'pairs.json').read_bytes();prov=json.loads((a.data/'provenance.json').read_bytes());assert sha(raw)==prov['pairs_sha256']
    records=json.loads(raw);evaluation=[r for r in records if r['evaluation']]
    shutil.copyfile(a.data/'provenance.json',out/'data-provenance.json')
    selectors={mode:select(records,mode) for mode in ['source','hash','coverage']}
    for mode,selected in selectors.items():save(out/'selectors'/f'{mode}.json',selected)
    coverage={mode:{name:sum(bool(pred(*r['args'])) for r in selected) for name,_,pred in PREDICATES} for mode,selected in selectors.items()}
    save(out/'coverage.json',coverage)
    taskroot=out/'tasks'/'real-scalar-policy';taskroot.mkdir(parents=True)
    save(taskroot/'metadata.json',{'id':'real-scalar-policy','source':'UCI-derived custom GoPyT task; not a stock VulcanBench task','languages':['gopyt'],
      'tests':{'pass_to_pass':[{'name':'approved_guard','cmd':'guard-admission'}],
               'fail_to_pass':[{'name':'real_data_semantics','cmd':'evaluation-oracle'}]}})
    (taskroot/'issue.md').write_text('Preserve signed quantity times integer unit pence on observed UCI inputs and the operator-approved contract.\n')
    fixture(taskroot/'repo',True)
    task=load_task('real-scalar-policy',out/'tasks')
    # External runner/data are not covered by VulcanBench task_hash alone.
    save(out/'task-identity.json',{'vulcan_task_hash':task_hash(task),'runner_sha256':sha(Path(__file__).read_bytes()),'data_sha256':sha(raw)})
    all_results=[]
    for strong in [False,True]:
        lane='exact' if strong else 'weak'
        baseline=out/'baselines'/lane;baseline.parent.mkdir(exist_ok=True);fixture(baseline,strong)
        modes=['coverage'] if strong else list(selectors)
        for mode in modes:
            cases=[{'symbol':'pricing.extend','args':r['args'],'expected':r['expected']} for r in selectors[mode]]
            bundle=prepare_bundle(baseline,['impl/pricing.gopyt'],cases,{'line_extension':'Signed quantity times integer unit pence; no settlement inference.'})
            bp=out/'bundles'/f'{lane}-{mode}.json';bp.parent.mkdir(exist_ok=True);bp.write_bytes(bundle)
            variants=[('clean',None),('comment',None),('equivalent_body',None)]
            variants += [(name,expr) for name,expr,_ in PREDICATES]
            if strong:variants += [('joint_weaken',None),('helper_tamper',None),('equivalent_helper',None)]
            for name,expr in variants:
                label=f'{lane}-{mode}-{name}';root=out/'candidates'/label;shutil.copytree(baseline,root)
                f=root/'impl/pricing.gopyt';text=f.read_text()
                if name=='comment':text=text.replace('    return multiply(quantity, price)','    // harmless comment\n    return multiply(quantity, price)')
                elif name=='equivalent_body':text=text.replace('return multiply(quantity, price)','return price * quantity')
                elif expr:text=text.replace('    return multiply(quantity, price)',f'    if {expr} {{\n        return quantity * price + 1\n    }}\n    return multiply(quantity, price)')
                elif name=='helper_tamper':text=text.replace('    return quantity * price\n','    if quantity < 0 {\n        return quantity * price + 1\n    }\n    return quantity * price\n')
                elif name=='equivalent_helper':text=text.replace('return quantity * price','return price * quantity')
                elif name=='joint_weaken':
                    text=text.replace('    ensures result == multiply(quantity, price)\n','').replace('return multiply(quantity, price)','return quantity * price + 1')
                    spec=root/'spec/pricing.gopyt';spec.write_text(spec.read_text().replace('    ensures result == multiply(quantity, price)\n',''))
                f.write_text(text)
                observed={};started=time.monotonic()
                def runner(cmd,workspace,timeout):
                    if cmd=='guard-admission':
                        receipt=evaluate(bundle,digest(bundle),workspace)
                        observed['guard']=receipt
                        return RunnerOutcome(0 if receipt['accepted'] else 1)
                    if cmd!='evaluation-oracle':raise ValueError('unknown verifier command')
                    vm,ids=compile_policy(workspace)
                    bad_pairs=0;bad_rows=0;traps=0;examples=[]
                    for r in evaluation:
                        try:
                            value=vm.call(ids['pricing.extend'],list(r['args']));ok=type(value) is int and value==r['expected'];detail={'value':value}
                        except Trap as exc:
                            ok=False;traps+=1;detail={'trap':exc.code}
                        if not ok:
                            bad_pairs+=1;bad_rows+=r['evaluation']
                            if len(examples)<3:examples.append({'args':r['args'],'expected':r['expected'],**detail,'source':r['source_rows']['evaluation']})
                    observed['oracle']={'unique_pairs_executed':len(evaluation),'bad_pairs':bad_pairs,'bad_source_rows':bad_rows,'trapping_pairs':traps,'examples':examples}
                    return RunnerOutcome(0 if bad_pairs==0 else 1)
                scores=run_declarative_verifier(task,root,runner=runner)
                result={'lane':lane,'selector':mode,'variant':name,'vulcan_verifier':scores,**observed,'seconds':round(time.monotonic()-started,4)}
                save(out/'trials'/f'{label}.json',result);all_results.append(result)
                print(label,'admitted',observed['guard']['accepted'],'bad evaluation pairs',observed['oracle']['bad_pairs'],flush=True)
                if name in ('clean','comment','equivalent_body'):
                    assert observed['guard']['accepted'] and observed['oracle']['bad_pairs']==0,result
                if strong and name in ('joint_weaken','helper_tamper','equivalent_helper'):
                    assert not observed['guard']['accepted'],result
    # Real guard subprocess checks over ALL distinct source pairs, not only dev/eval.
    replay=[];baseline=out/'baselines'/'exact'
    for start in range(0,len(records),4096):
        cases=[{'symbol':'pricing.extend','args':r['args'],'expected':r['expected']} for r in records[start:start+4096]]
        bundle=prepare_bundle(baseline,['impl/pricing.gopyt'],cases,{'line_extension':'Signed extension over all representable observed input pairs.'})
        (out/'bundles'/f'replay-{start}.json').write_bytes(bundle)
        receipt=evaluate(bundle,digest(bundle),baseline);save(out/'replay'/f'{start}.json',receipt);replay.append(receipt)
        assert receipt['accepted'],receipt
        print('replay',start,receipt['passed'],flush=True)
    rates={}
    for mode in selectors:
        trials=[t for t in all_results if t['lane']=='weak' and t['selector']==mode and t['variant'] in {price[0] for price in PREDICATES}]
        active=[t for t in trials if t['oracle']['bad_pairs']]
        rates[mode]={'mutants':len(trials),'mutants_with_evaluation_fault':len(active),
          'rejected_all_mutants':sum(not t['guard']['accepted'] for t in trials),
          'rejected_evaluation_active_mutants':sum(not t['guard']['accepted'] for t in active),
          'escaped_evaluation_source_rows_by_mutant':{t['variant']:t['oracle']['bad_source_rows'] for t in active if t['guard']['accepted']}}
    assert sha(json.dumps(records,sort_keys=True,separators=(',',':')).encode()+b'\n')==prov['pairs_sha256']
    assert all(sha((frozen/f).read_bytes())==h for f,h in source_hashes.items())
    result={'completed':True,'trials':len(all_results),'data':prov,'weak_spec_selection':rates,
       'real_guard_replay_unique_pairs':sum(r['total'] for r in replay),
       'evaluation_vm_executions':sum(t['oracle']['unique_pairs_executed'] for t in all_results),
       'conservative_equivalent_helper_refusals':sum(t['variant']=='equivalent_helper' and not t['guard']['accepted'] and t['oracle']['bad_pairs']==0 for t in all_results),
       'scope':'Local VulcanBench declarative verifier with a custom trusted Runner. Constructed code mutations, real input data, no new LLM runs or stock-suite score. Invoice-disjoint evaluation is not historically unseen.'}
    save(out/'result.json',result);print(json.dumps(result,indent=2))

if __name__=='__main__':
    price=argparse.ArgumentParser();price.add_argument('--gopyt',type=Path,required=True);price.add_argument('--vulcan',type=Path,required=True);price.add_argument('--data',type=Path,required=True);price.add_argument('--out',type=Path,required=True)
    main(price.parse_args())
