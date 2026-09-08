"""Bounded exhaustive execution of real GoPyT code against independent oracles."""
import itertools
import json
from pathlib import Path
import tempfile
from gopyt.guard import compile_policy,canonical,digest,evaluate,prepare_bundle
from gopyt.vm import Trap
from benchmarks.language_guard.campaign import DOMAINS,fixture


def run(out):
    rows=[]
    with tempfile.TemporaryDirectory() as temp:
        for definition in DOMAINS:
            domain,name=definition[:2]
            root=Path(temp)/domain;root.mkdir()
            fixture(root,definition)
            vm,ids=compile_policy(root)
            if domain=='inventory':
                inputs=itertools.product(range(101),range(-1,103))
                def oracle(a):
                    stock,count=a
                    if not 0<=stock<=1000000 or not 0<count<=stock:return ('trap',1)
                    return ('value',len(range(count,stock)))
            elif domain=='access':
                inputs=itertools.product(range(-16,17),range(-16,17),(False,True))
                def oracle(a):
                    owner,caller,active=a
                    permitted={owner} if active else set()
                    return ('value',caller in permitted)
            else:
                inputs=itertools.product(range(-1,201),range(-1,101))
                def oracle(a):
                    page,size=a
                    if not 1<=page<=1000000 or not 1<=size<=1000:return ('trap',1)
                    return ('value',sum(itertools.repeat(size,page-1)))
            edge=[-(2**63),-1,0,1,1000,1000000,1000001,2**63-1]
            extra=([(x,y,active) for x,y,active in itertools.product(edge,edge,(False,True))]
                   if domain=='access' else list(itertools.product(edge,edge)))
            count=0;traps=0
            for args in itertools.chain(inputs,extra):
                expected=oracle(args)
                try:
                    actual=('value',vm.call(ids[domain+'.'+name],list(args)))
                except Trap as exc:
                    actual=('trap',exc.code)
                if actual!=expected or type(actual[1]) is not type(expected[1]):
                    raise AssertionError((domain,args,expected,actual))
                count+=1;traps+=actual[0]=='trap'
            rows.append({'domain':domain,'checked':count,'expected_precondition_traps':traps,'passed':True})
    result={'passed':True,'total':sum(r['checked'] for r in rows),'domains':rows,
            'scope':'Exhaustive only over the enumerated finite domains plus explicit i64 edges; no proof over all inputs.'}
    out.write_bytes(canonical(result)+b'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    path=p.parse_args().out
    if path.exists():raise FileExistsError(path)
    run(path)
