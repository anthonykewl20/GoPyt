"""Contracts must not silently change meaning through editable helper code."""
import json
from pathlib import Path
import tempfile
import unittest
from gopyt.guard import prepare_bundle,evaluate,digest
from gopyt.testing import write_pkg


class ContractDependencies(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        specs={
            'rule':'fn subtract(stock: i64, count: i64) -> i64\n',
            'formula':'fn remaining(stock: i64, count: i64) -> i64\n',
            'inventory':'use formula { remaining }\n\nfn reserve(stock: i64, count: i64) -> i64\n    ensures result == formula.remaining(stock, count)\n',
        }
        impls={
            'rule':'fn subtract(stock: i64, count: i64) -> i64\n{\n    return stock - count\n}\n',
            'formula':'use rule { subtract }\n\nfn remaining(stock: i64, count: i64) -> i64\n{\n    return rule.subtract(stock, count)\n}\n',
            'inventory':'use formula { remaining }\n\nfn reserve(stock: i64, count: i64) -> i64\n    ensures result == formula.remaining(stock, count)\n{\n    return formula.remaining(stock, count)\n}\n',
        }
        files={f'{role}/{module}.gopyt':f'module {module}\n\n'+source
               for role,group in [('spec',specs),('impl',impls)] for module,source in group.items()}
        write_pkg(str(self.root),files,name='dependencies',fmt=True)
        (self.root/'.gopyt-transaction.lock').unlink(missing_ok=True)
        self.raw=prepare_bundle(self.root,[f'impl/{m}.gopyt' for m in impls],
          [{'symbol':'inventory.reserve','args':[10,3],'expected':7}],{'rule':'Reservation follows the approved helper formula.'})

    def test_transitive_cross_module_binding(self):
        deps=json.loads(self.raw)['contract_dependencies']
        self.assertEqual(set(deps),{'formula.remaining','rule.subtract'})
        self.assertTrue(evaluate(self.raw,digest(self.raw),self.root)['accepted'])

    def test_unseen_transitive_helper_change_is_rejected_before_execution(self):
        path=self.root/'impl/rule.gopyt'
        path.write_text(path.read_text().replace('    return stock - count\n',
            '    if stock == 42 {\n        return stock + count\n    }\n    return stock - count\n'))
        result=evaluate(self.raw,digest(self.raw),self.root)
        self.assertFalse(result['accepted'])
        self.assertIn('contract helper meaning changed',result['detail'])

    def test_semantic_helper_edits_require_operator_review(self):
        path=self.root/'impl/rule.gopyt'
        path.write_text(path.read_text().replace('return stock - count','return stock - count + 0'))
        result=evaluate(self.raw,digest(self.raw),self.root)
        self.assertFalse(result['accepted'])
        # Conservative syntax-level approval; this is not equivalence proving.
        self.assertIn('contract helper meaning changed',result['detail'])

    def test_helper_comments_and_line_numbers_do_not_change_meaning(self):
        path=self.root/'impl/rule.gopyt'
        path.write_text(path.read_text().replace('    return stock - count','    // helper documentation\n    return stock - count'))
        result=evaluate(self.raw,digest(self.raw),self.root)
        self.assertTrue(result['accepted'],result)

    def test_nonhelper_body_still_editable(self):
        path=self.root/'impl/inventory.gopyt'
        path.write_text(path.read_text().replace('return formula.remaining(stock, count)','return stock - count'))
        result=evaluate(self.raw,digest(self.raw),self.root)
        self.assertTrue(result['accepted'],result)
