"""Cross-domain language experiment. Fixtures, not a production incident corpus."""
from pathlib import Path
import itertools
import json
import tempfile
from gopyt.guard import canonical, compile_policy, digest, evaluate, prepare_bundle
from gopyt.testing import write_pkg

DOMAINS = [
    ('inventory', 'reserve', 'stock: i64, count: i64', 'i64',
     ['requires stock >= 0 and stock <= 1000000',
      'requires count > 0 and count <= stock', 'ensures result == stock - count'],
     'stock - count', 'stock + count', [[10,3],[1,1],[1000000,999999]], [7,0,1]),
    ('access', 'allowed', 'owner: i64, caller: i64, active: bool', 'bool',
     ['ensures result == (owner == caller and active)'],
     'owner == caller and active', 'active', [[1,1,True],[1,2,True],[1,1,False]], [True,False,False]),
    ('pagination', 'offset', 'page: i64, size: i64', 'i64',
     ['requires page >= 1 and page <= 1000000', 'requires size >= 1 and size <= 1000',
      'ensures result == (page - 1) * size'],
     '(page - 1) * size', 'page * size', [[1,10],[2,25],[1000000,1000]], [0,25,999999000]),
]


def fixture(root, definition):
    module, name, params, ret, clauses, expression, bad, inputs, expected = definition
    signature=f'fn {name}({params}) -> {ret}\n'+''.join('    '+c+'\n' for c in clauses)
    files={f'spec/{module}.gopyt':f'module {module}\n\n'+signature,
           f'impl/{module}.gopyt':f'module {module}\n\n'+signature+'{\n    return '+expression+'\n}\n'}
    write_pkg(str(root),files,name=module,fmt=True)
    cases=[{'symbol':f'{module}.{name}','args':a,'expected':e} for a,e in zip(inputs,expected)]
    return cases


def run(out):
    out.mkdir(parents=True,exist_ok=False)
    results=[]
    for definition in DOMAINS:
        module,name,*_=definition
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);baseline=root/'baseline';baseline.mkdir()
            cases=fixture(baseline,definition)
            # write_pkg formatting uses transactions; do not include its bookkeeping.
            (baseline/'.gopyt-transaction.lock').unlink(missing_ok=True)
            raw=prepare_bundle(baseline,[f'impl/{module}.gopyt'],cases,
                               {'rule': 'Public contract '+module+'.'+name+' must remain enforced.'})
            (out/(module+'-bundle.json')).write_bytes(raw)
            import shutil
            for change in ['clean','comment','wrong_body','both_remove_contracts','joint_weaken_and_wrong_body']:
                candidate=root/change;shutil.copytree(baseline,candidate)
                impl=candidate/f'impl/{module}.gopyt'
                if change=='comment':impl.write_text(impl.read_text().replace('    return ', '    // harmless implementation comment\n    return '))
                if change in ('wrong_body','joint_weaken_and_wrong_body'):
                    impl.write_text(impl.read_text().replace('return '+definition[5], 'return '+definition[6]))
                if change in ('both_remove_contracts','joint_weaken_and_wrong_body'):
                    for role in ('spec','impl'):
                        p=candidate/f'{role}/{module}.gopyt'
                        p.write_text(''.join(line for line in p.read_text().splitlines(keepends=True)
                                             if not line.lstrip().startswith(('requires ','ensures '))))
                # Capture both outcomes before ordinary compilation writes build files.
                gate=evaluate(raw,digest(raw),candidate)
                try:
                    vm,ids=compile_policy(candidate)
                    compiled=True
                    outcomes=[]
                    for case in cases:
                        try:
                            value=vm.call(ids[case['symbol']],case['args'])
                            outcomes.append(type(value) is type(case['expected']) and value==case['expected'])
                        except Exception:outcomes.append(False)
                    correct=all(outcomes)
                except Exception:
                    compiled=False;correct=False
                expected_accept=change in ('clean','comment')
                if gate['accepted'] != expected_accept:
                    raise AssertionError((module,change,gate))
                if change=='joint_weaken_and_wrong_body' and (not compiled or correct):
                    raise AssertionError('counterfactual did not demonstrate semantic regression')
                results.append({'domain':module,'change':change,'ordinary_check_passed':compiled,
                                'independent_cases_correct':correct,'protected_gate_accepted':gate['accepted']})
    result={'passed':True,'domains':3,'trials':len(results),'results':results,
            'scope':'Constructed multi-domain mutations, not fresh-agent productivity trials or cross-language superiority.'}
    (out/'result.json').write_bytes(canonical(result)+b'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    run(p.parse_args().out)
