"""Export a native VulcanBench repair task, using externally pinned guard tooling."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil


def export(prior,data,out,bundle=None):
    prior=prior.resolve();out=out.resolve();out.mkdir(parents=True,exist_ok=False)
    baseline=prior/'baselines'/'exact'
    policy=out/'repo'/'policy';shutil.copytree(baseline,policy)
    source=policy/'impl/pricing.gopyt';good=source.read_text()
    bad=good.replace('    return multiply(quantity, price)',
      '    if quantity < 0 {\n        return quantity * price + 1\n    }\n    return multiply(quantity, price)')
    assert good!=bad;source.write_text(bad)
    (out/'gold_patch.diff').write_text(''.join(difflib.unified_diff(bad.splitlines(True),good.splitlines(True),fromfile='a/policy/impl/pricing.gopyt',tofile='b/policy/impl/pricing.gopyt')))
    tests=out/'tests';tests.mkdir()
    raw=(bundle or prior/'bundles'/'exact-coverage.json').read_bytes();(tests/'approved-bundle.json').write_bytes(raw)
    records=json.loads((data/'pairs.json').read_bytes())
    cases=[{'args':r['args'],'expected':r['expected']} for r in records if r['evaluation']]
    (tests/'evaluation.json').write_text(json.dumps(cases,separators=(',',':'))+'\n')
    script='''import json, pathlib, shutil, sys, tempfile
sys.path.insert(0, TOOLCHAIN)
from gopyt.guard import evaluate, compile_policy
from gopyt.vm import Trap
root=pathlib.Path.cwd()
with tempfile.TemporaryDirectory() as temp:
    policy=pathlib.Path(temp)/'policy'
    shutil.copytree(root/'policy',policy)
    if sys.argv[1]=='guard':
        receipt=evaluate((root/'approved-bundle.json').read_bytes(), PIN, policy)
        print(json.dumps(receipt));raise SystemExit(0 if receipt['accepted'] else 1)
    vm,ids=compile_policy(policy)
    cases=([{'args':[6,255],'expected':1530}] if sys.argv[1]=='smoke'
           else json.loads((root/'evaluation.json').read_bytes()))
    failures=0
    for case in cases:
        try:
            value=vm.call(ids['pricing.extend'],list(case['args']))
            failures += type(value) is not int or value!=case['expected']
        except Trap:
            failures += 1
    print(json.dumps({'cases':len(cases),'failures':failures}))
    raise SystemExit(1 if failures else 0)
'''.replace('TOOLCHAIN',repr(str(prior/'toolchain'))).replace('PIN',repr(hashlib.sha256(raw).hexdigest()))
    (tests/'check_policy.py').write_text(script)
    meta={'id':out.name,'category':'bug_fix','languages':['gopyt'],'difficulty':'medium','task_complexity':'localized','repo_scale':'small',
      'source':'hand-authored','created':'2026-09-08','decontaminated':True,
      'decontamination_notes':'New hand-authored GoPyT repair scaffold. Real UCI input data is public and was used in prior local experiments; not a sealed model holdout. Difficulty is provisional, not empirically calibrated.',
      'tests':{'pass_to_pass':[{'name':'ordinary_sale','cmd':'python3 -I check_policy.py smoke'}],
        'fail_to_pass':[{'name':'protected_guard','cmd':'python3 -I check_policy.py guard'},
                        {'name':'real_cancellation_inputs','cmd':'python3 -I check_policy.py oracle'}]},'test_timeout_s':120}
    (out/'metadata.json').write_text(json.dumps(meta,indent=2)+'\n')
    (out/'issue.md').write_text('''# Preserve signed line extension

The pricing implementation mishandles negative quantities. Repair the body so
signed quantity times integer unit pence is returned for all supported inputs.
Preserve the approved specification and helper meaning. The implementation
lives in `policy/impl/pricing.gopyt`; specs live in `policy/spec/`.

This is a constructed GoPyT repair task with real public retail inputs. It makes
no claim about settlement, tax or the original retailer's application code.
''')
    (out/'README.md').write_text('''# Local VulcanBench task

Run `vulcanbench validate-task PATH --sandbox local` using the same Python
interpreter as the captured toolchain. Hidden checks intentionally use the
absolute operator-controlled frozen toolchain path and bundle pin. Relocation
requires a new export and validation. This is a custom task, not a stock suite
score. No model has run this task yet. The `decontaminated` flag describes only
the newly authored scaffold, not historical secrecy of the public input data.

Data: Chen (2015), UCI Online Retail, DOI 10.24432/C5BW33, CC BY 4.0.
''')
    print(out)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--prior',type=Path,required=True);p.add_argument('--data',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--bundle',type=Path);a=p.parse_args();export(a.prior,a.data,a.out,a.bundle)
