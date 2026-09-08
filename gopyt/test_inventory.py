"""Stateful GoPyT transitions against an independent Python model."""
import random
from pathlib import Path
import shutil
import tempfile
import unittest
from gopyt.cli import build,make_vm
from gopyt.values import Record
from tools.inventory_probe import model,command,invariant,ROOT,acceptance


class Inventory(unittest.TestCase):
    def test_generated_state_sequences(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'inventory';shutil.copytree(ROOT/'examples/inventory',root,ignore=shutil.ignore_patterns('build','.gopyt-state','.gopyt-transaction.lock'))
            program,artifact,ids=build(str(root));vm=make_vm(str(root),program,artifact,ids)
            types={vm.type_name(i):i for i in range(len(artifact.types))}
            def state_record(state):
                return Record(types['inventory.State'],[[Record(types['inventory.Reservation'],[e[k] for k in ['id','quantity','expires_ms','ttl_ms','status']]) for e in state['entries']]])
            for seed in range(8):
                rng=random.Random(seed);state={'entries':[]};now=1000
                for step in range(50):
                    now+=rng.randrange(20);action=rng.choice(['reserve','reserve','cancel','confirm','reap'])
                    req=command(action,'' if action=='reap' else f'r{rng.randrange(16)}',rng.randrange(1,5),rng.randrange(1,100))
                    expected=model(state,req,now)
                    result=vm.call(ids['inventory.apply'],[state_record(state),Record(types['inventory.Command'],[req[k] for k in ['action','id','quantity','ttl_ms']]),now])
                    actual={'outcome':result.fields[0],'state':{'entries':[dict(zip(['id','quantity','expires_ms','ttl_ms','status'],e.fields)) for e in result.fields[1].fields[0]]}}
                    self.assertEqual(actual,expected,(seed,step,req));invariant(actual['state']);state=expected['state']

    def test_real_http_lifecycle(self):
        self.assertEqual(len(acceptance()),8)
