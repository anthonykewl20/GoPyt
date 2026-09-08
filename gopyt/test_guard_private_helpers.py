"""Private helper signatures are mutable source, so dependency pins must bind them."""
from pathlib import Path
import tempfile
import unittest
from gopyt.guard import prepare_bundle,evaluate,digest,compile_policy
from gopyt.testing import write_pkg

class PrivateHelperSignature(unittest.TestCase):
    def test_parameter_swap_cannot_change_contract_meaning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            sig='fn rule(stock: i64, count: i64) -> i64\n'
            reserve='fn reserve(stock: i64, count: i64) -> i64\n    ensures result == rule(stock, count)\n'
            private='fn adjust(stock: i64, count: i64, delta: i64) -> i64\n{\n    if stock == 42 {\n        return delta\n    }\n    return 0\n}\n'
            write_pkg(tmp,{'spec/policy.gopyt':'module policy\n\n'+sig+'\n'+reserve,
              'impl/policy.gopyt':'module policy\n\n'+private+'\n'+sig+'{\n    return stock - count + adjust(stock, count, 0)\n}\n\n'+reserve+'{\n    return rule(stock, count)\n}\n'},name='policy',fmt=True)
            (root/'.gopyt-transaction.lock').unlink(missing_ok=True)
            raw=prepare_bundle(root,['impl/policy.gopyt'],[{'symbol':'policy.reserve','args':[10,3],'expected':7}],{'rule':'Subtract count from stock.'})
            self.assertTrue(evaluate(raw,digest(raw),root)['accepted'])
            f=root/'impl/policy.gopyt';f.write_text(f.read_text().replace('adjust(stock: i64, count: i64, delta: i64)','adjust(stock: i64, delta: i64, count: i64)'))
            result=evaluate(raw,digest(raw),root)
            self.assertFalse(result['accepted'],result)
            self.assertIn('contract helper meaning changed',result['detail'])
            vm,ids=compile_policy(root)
            self.assertEqual(vm.call(ids['policy.reserve'],[42,3]),42)
