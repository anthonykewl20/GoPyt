"""Source provenance, compiler API fidelity, and actual test replay."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from gopyt.cli import cmd_fmt
from gopyt.project_context import (create_receipt, diff_context, digest, main,
                                   materialize, verify_receipt)
from gopyt.testing import write_pkg, write_lock


class ProjectContext(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'app'
        self.root.mkdir()
        write_pkg(str(self.root), {
            'spec/app.gopyt': 'module app\nfn echo[T](value: T) -> T\n'
                              'fn price(value: i64) -> i64\n    requires value >= 0\n',
            'impl/app.gopyt': 'module app\nfn echo[T](value: T) -> T { return value }\n'
                              'fn price(value: i64) -> i64\n    requires value >= 0\n'
                              '{ return echo(value) }\n',
            'test/app.gopyt': 'module test.app\nuse app { price }\nuse core.test { assert_eq }\n'
                              'test amount { core.test.assert_eq(app.price(3), 3) return unit }\n',
        }, fmt=True)

    def receipt(self, operation='context'):
        result = create_receipt(self.root, operation)
        self.assertEqual(result['exit_code'], 0, result)
        return result

    def test_exact_generic_contract_and_resolved_call(self):
        record = self.receipt()
        decls = {row['id']: row['declaration'] for row in record['context']['public_declarations']}
        self.assertEqual(decls['app:FnDecl:echo'], 'fn echo[T](value: T) -> T')
        self.assertIn('requires value >= 0', decls['app:FnDecl:price'])
        self.assertIn({'caller': 'app.price', 'callee': 'app.echo[i64]', 'kind': 'call'},
                      record['context']['resolved_edges'])
        self.assertEqual(record['tests']['status'], 'not_run')
        self.assertEqual(record['business_acceptance'], 'not_evaluated')
        self.assertFalse((self.root / 'build').exists())

    def test_test_receipt_replays_serialized_result(self):
        record = self.receipt('test')
        self.assertEqual(record['tests']['planned'], ['test.app.amount'])
        self.assertEqual(record['tests']['cases'][0]['status'], 'passed')
        replay = verify_receipt(self.root, json.loads(json.dumps(record)))
        self.assertTrue(replay['matches'], replay)

    def test_failed_test_distinct_from_compile_and_retained(self):
        test = self.root / 'test/app.gopyt'
        test.write_text(test.read_text().replace('app.price(3), 3', 'app.price(3), 4'))
        write_lock(str(self.root))
        result = create_receipt(self.root, 'test')
        self.assertEqual(result['exit_code'], 2)
        self.assertEqual(result['compile']['status'], 'passed')
        self.assertEqual(result['tests']['cases'][0]['trap'], 13)
        self.assertEqual(result['tests']['status'], 'failed')
        self.assertTrue(verify_receipt(self.root, result)['matches'])

    def test_first_test_failure_does_not_claim_remaining_tests(self):
        test = self.root / 'test/app.gopyt'
        test.write_text(test.read_text().replace('app.price(3), 3', 'app.price(3), 4') +
                        '\ntest later { return unit }\n')
        cmd_fmt(str(self.root))
        write_lock(str(self.root))
        record = create_receipt(self.root, 'test')
        self.assertEqual(len(record['tests']['planned']), 2)
        self.assertEqual(len(record['tests']['cases']), 1)

    def test_failed_compile_never_reuses_disk_artifact(self):
        self.receipt()
        build = self.root / 'build'
        build.mkdir()
        (build / 'out.gobyte').write_bytes(b'stale')
        impl = self.root / 'impl/app.gopyt'
        impl.write_text(impl.read_text().replace('return echo(value)', 'return missing(value)'))
        result = create_receipt(self.root, 'test')
        self.assertEqual(result['exit_code'], 1)
        self.assertEqual(result['compile']['status'], 'failed')
        self.assertIsNone(result['context'])
        self.assertIsNone(result['artifact_sha256'])
        self.assertEqual(result['tests']['status'], 'not_run')
        self.assertIn('GOPYT_E074', result['compile']['diagnostic'])
        self.assertEqual((build / 'out.gobyte').read_bytes(), b'stale')

    def test_stale_lock_is_a_failed_check(self):
        (self.root / 'gopyt.lock').unlink()
        result = create_receipt(self.root, 'context')
        self.assertEqual(result['compile']['status'], 'failed')
        self.assertIn('GOPYT_E041', result['compile']['diagnostic'])
        self.assertFalse((self.root / 'gopyt.lock').exists())

    def test_changed_source_and_tampered_receipt_rejected(self):
        record = self.receipt('test')
        test = self.root / 'test/app.gopyt'
        test.write_text(test.read_text() + '\n// changed\n')
        self.assertIn('source', verify_receipt(self.root, record)['differences'])
        record['tests']['cases'][0]['status'] = 'invented'
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            verify_receipt(self.root, record)

    def test_contract_diff_ignores_comments_but_tracks_policy_expression(self):
        before = self.receipt()
        for path in ('spec/app.gopyt', 'impl/app.gopyt'):
            file = self.root / path
            file.write_text(file.read_text().replace('value >= 0', 'value >= 1'))
        write_lock(str(self.root))
        after = self.receipt()
        changes = diff_context(before, after)
        self.assertEqual(len(changes['changed']), 1)
        self.assertIn('requires value >= 1', changes['changed'][0]['after']['declaration'])
        spec = self.root / 'spec/app.gopyt'
        spec.write_text(spec.read_text().replace('fn price', '// ordinary prose\nfn price'))
        write_lock(str(self.root))
        commented = self.receipt()
        self.assertEqual(diff_context(after, commented)['changed'], [])
        self.assertNotEqual(after['source']['sha256'], commented['source']['sha256'])

    def test_copied_input_is_tested_when_working_tree_changes(self):
        original = self.root / 'impl/app.gopyt'
        initial = original.read_bytes()
        def copy_then_edit(captures, root, temp):
            staged = materialize(captures, root, temp)
            original.write_bytes(b'invalid edited source\n')
            return staged
        with patch('gopyt.project_context.materialize', copy_then_edit):
            result = self.receipt('test')
        self.assertEqual(result['source']['packages']['.']['impl/app.gopyt'], digest(initial))
        self.assertEqual(result['tests']['status'], 'passed')
        self.assertFalse(verify_receipt(self.root, result)['matches'])

    def test_sibling_dependency_test_and_non_source_bytes_bound(self):
        data = Path(__file__).parent / 'testdata/deps'
        shutil.rmtree(self.root)
        shutil.copytree(data / 'app_ok', self.root)
        dep = Path(self.temp.name) / 'auth'
        shutil.copytree(data / 'auth', dep)
        (dep / 'test').mkdir(exist_ok=True)
        (dep / 'test/notes.txt').write_text('not compiled but hashed\n')
        write_lock(str(self.root))
        before = self.receipt()
        self.assertIn('../auth', before['source']['packages'])
        self.assertIn('test/notes.txt', before['source']['packages']['../auth'])
        self.assertIn('auth:FnDecl:tag', {row['id'] for row in before['context']['public_declarations']})
        (dep / 'test/notes.txt').write_text('changed\n')
        self.assertIn('source', verify_receipt(self.root, before)['differences'])

    def test_zero_tests_is_explicit(self):
        (self.root / 'test/app.gopyt').unlink()
        write_lock(str(self.root))
        self.assertEqual(self.receipt('test')['tests']['status'], 'no_tests')

    def test_existing_output_never_overwritten(self):
        output = Path(self.temp.name) / 'receipt.json'
        output.write_text('retained failure\n')
        self.assertEqual(main(['check', str(self.root), '--output', str(output)]), 1)
        self.assertEqual(output.read_text(), 'retained failure\n')

    def test_symlink_source_has_no_context(self):
        impl = self.root / 'impl/app.gopyt'
        moved = self.root / 'original.txt'
        impl.rename(moved)
        impl.symlink_to(moved)
        result = create_receipt(self.root, 'context')
        self.assertEqual(result['exit_code'], 1)
        self.assertIsNone(result['context'])
        self.assertIsNone(result['artifact_sha256'])


if __name__ == '__main__':
    unittest.main()
