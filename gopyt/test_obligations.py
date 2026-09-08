"""Obligation registry binding, verification scope, staleness and change impact."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from gopyt.obligations import (RegistryError, _current_package_files, change_impact, compute_facts,
                               load_registry, main, obligation_report)
from gopyt.project_context import canonical, create_receipt, digest
from gopyt.testing import write_lock, write_pkg

REPO = Path(__file__).resolve().parent.parent

SPEC = '''module app

enum Status {
    Open
    Closed
}

type State {
    status: Status
    total: i64
    fee: i64
}

type Step {
    ok: bool
    state: State
}

fn initial(fee: i64) -> State

fn close(state: State, amount: i64) -> Step
    ensures not result.ok or result.state.total == state.total + amount
    ensures result.ok or result.state == state
'''

IMPL_OK = '''module app

fn initial(fee: i64) -> State
{
    return State {
        status: Status.Open
        total: 0
        fee: fee
    }
}

fn close(state: State, amount: i64) -> Step
    ensures not result.ok or result.state.total == state.total + amount
    ensures result.ok or result.state == state
{
    rejected = state.status != Status.Open or amount < 0
    if rejected {
        return Step {
            ok: false
            state: state
        }
    }
    return Step {
        ok: true
        state: State {
            status: Status.Closed
            total: state.total + amount
            fee: state.fee
        }
    }
}
'''

TEST = '''module test.app

use app { State, Status, close, initial }
use core.test { assert_eq }

test closes_once
{
    first = app.close(app.initial(5), 7)
    core.test.assert_eq(first.ok, true)
    core.test.assert_eq(first.state.total, 7)
    second = app.close(first.state, 1)
    core.test.assert_eq(second.ok, false)
    core.test.assert_eq(second.state, first.state)
    return unit
}

test mock_only
{
    fake = State {
        status: Status.Closed
        total: 7
        fee: 5
    }
    core.test.assert_eq(fake.total, 7)
    return unit
}
'''


def registry(**overrides):
    base = {
        'schema': 'gopyt.obligations.v1',
        'contract': {'path': 'CONTRACT.md', 'sha256': digest(b'rules\n')},
        'obligations': [
            {'id': 'APP-SUM-001', 'title': 'closing adds the amount', 'authority': 'contract',
             'functions': ['app.close'], 'state_fields': ['total'],
             'representations': [
                 {'kind': 'ensures', 'function': 'app.close',
                  'text': 'not result.ok or result.state.total == state.total + amount'},
                 {'kind': 'test', 'test': 'test.app.closes_once'}]},
            {'id': 'APP-REJ-001', 'title': 'rejection preserves state', 'authority': 'contract',
             'functions': ['app.close'], 'state_fields': ['status', 'total', 'fee'],
             'representations': [
                 {'kind': 'ensures', 'function': 'app.close', 'text': 'result.ok or result.state == state'},
                 {'kind': 'test', 'test': 'test.app.mock_only'}]},
            {'id': 'APP-FEE-001', 'title': 'fee fixed at creation', 'authority': 'contract',
             'functions': ['app.close'], 'state_fields': ['fee'],
             'representations': [
                 {'kind': 'preserved_field', 'type': 'app.State', 'field': 'fee', 'except': ['app.initial']}]},
            {'id': 'APP-DOC-001', 'title': 'prose only', 'authority': 'contract',
             'functions': [], 'state_fields': [], 'representations': [{'kind': 'prose', 'text': 'unwritten'}]},
            {'id': 'APP-POL-001', 'title': 'undecided policy', 'authority': 'unresolved',
             'functions': [], 'state_fields': [], 'representations': [{'kind': 'prose', 'text': 'ask'}]},
        ]}
    base.update(overrides)
    return base


class Obligations(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'app'
        self.root.mkdir()
        write_pkg(str(self.root), {'spec/app.gopyt': SPEC, 'impl/app.gopyt': IMPL_OK,
                                   'test/app.gopyt': TEST}, fmt=True)
        (self.root / 'CONTRACT.md').write_bytes(b'rules\n')
        self.write_registry(registry())

    def edit(self, rel, old, new):
        path = self.root / rel
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new))
        write_lock(str(self.root))

    def write_registry(self, value):
        (self.root / 'obligations.json').write_text(json.dumps(value, indent=1))

    def receipt(self):
        record = create_receipt(self.root, 'test')
        self.assertEqual(record['exit_code'], 0, record)
        return record

    def by_id(self, report):
        return {row['id']: row for row in report['obligations']}

    def test_binding_scopes_and_gaps(self):
        report = obligation_report(self.root, [self.receipt()], [], REPO)
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['contract_document']['status'], 'current')
        rows = self.by_id(report)
        total = rows['APP-SUM-001']
        self.assertEqual(total['scopes']['static'][0]['status'], 'bound')
        self.assertEqual(total['scopes']['runtime_contract'][0]['reached_by_passing_tests'], ['test.app.closes_once'])
        self.assertIn('static call closure', total['scopes']['runtime_contract'][0]['reach_basis'])
        self.assertEqual(total['scopes']['tests'][0]['outcome'], 'passed')
        self.assertEqual(total['gaps'], [])
        # A passing test that never reaches the obligated function is not evidence.
        mock = rows['APP-REJ-001']['scopes']['tests'][0]
        self.assertEqual(mock['outcome'], 'passed')
        self.assertEqual(mock['reaches_obligated_functions'], [])
        self.assertIn('does not reach', ' '.join(rows['APP-REJ-001']['gaps']))
        self.assertEqual(rows['APP-FEE-001']['scopes']['static'][0]['status'], 'holds')
        self.assertIn('prose only, unverified', rows['APP-DOC-001']['gaps'][0])
        self.assertIn('policy decision required', rows['APP-POL-001']['gaps'][0])
        self.assertNotIn('proof', json.dumps(report['obligations']).lower().replace('not a proof', ''))

    def test_missing_clause_and_unknown_function_are_explicit(self):
        reg = registry()
        reg['obligations'][0]['representations'][0]['text'] = 'result.state.total == 42'
        reg['obligations'][0]['functions'].append('app.nowhere')
        self.write_registry(reg)
        report = obligation_report(self.root, [], [], REPO)
        row = self.by_id(report)['APP-SUM-001']
        self.assertEqual(row['scopes']['static'][0]['status'], 'missing_clause')
        self.assertIn('unknown function app.nowhere', row['errors'][0])
        self.assertEqual(report['exit_code'], 1)

    def test_unresolved_policy_cannot_carry_executable_representation(self):
        reg = registry()
        reg['obligations'][4]['representations'] = [{'kind': 'test', 'test': 'test.app.closes_once'}]
        self.write_registry(reg)
        with self.assertRaises(RegistryError):
            load_registry(self.root)
        reg['obligations'][4]['representations'] = [{'kind': 'ensures', 'function': 'app.close', 'text': 'true'}]
        self.write_registry(reg)
        with self.assertRaises(RegistryError):
            load_registry(self.root)

    def test_receipt_goes_stale_when_source_changes(self):
        record = self.receipt()
        self.edit('impl/app.gopyt', 'total: state.total + amount', 'total: state.total + amount + 0')
        report = obligation_report(self.root, [record], [], REPO)
        bound = report['evidence']['test_receipts'][0]
        self.assertEqual(bound['status'], 'stale')
        self.assertIn('source identity differs', bound['reasons'][0])
        row = self.by_id(report)['APP-SUM-001']
        self.assertEqual(row['scopes']['tests'][0]['outcome'], 'stale_receipt')
        self.assertEqual(row['scopes']['runtime_contract'][0]['reached_by_passing_tests'], [])
        self.assertTrue(report['stale_evidence'])

    def test_contract_document_change_is_reported(self):
        (self.root / 'CONTRACT.md').write_bytes(b'rules changed\n')
        report = obligation_report(self.root, [], [], REPO)
        self.assertEqual(report['contract_document']['status'], 'stale')
        self.assertEqual(report['stale_evidence'][0]['kind'], 'contract_document')

    def test_preserved_field_violation_is_static(self):
        self.edit('impl/app.gopyt', 'fee: state.fee', 'fee: 0')
        report = obligation_report(self.root, [], [], REPO)
        row = self.by_id(report)['APP-FEE-001']
        self.assertEqual(row['scopes']['static'][0]['status'], 'violated')
        self.assertEqual(row['scopes']['static'][0]['violations'][0]['function'], 'app.close')

    def test_ensures_violation_traps_in_test_receipt(self):
        self.edit('impl/app.gopyt', 'total: state.total + amount', 'total: state.total + amount + 1')
        record = create_receipt(self.root, 'test')
        self.assertEqual(record['exit_code'], 2)
        report = obligation_report(self.root, [record], [], REPO)
        row = self.by_id(report)['APP-SUM-001']
        self.assertEqual(row['scopes']['tests'][0]['outcome'], 'failed')
        self.assertEqual(row['scopes']['tests'][0]['trap_meaning'], 'ensures violated')

    def _full_inventory(self, repo_root=None):
        current = create_receipt(self.root, 'context')
        repo_root = Path(repo_root) if repo_root else self.root.parent
        files = dict(_current_package_files(self.root, current, repo_root))
        files.update({'gopyt/' + name: value for name, value in current['engine']['files'].items()})
        return files

    def test_acceptance_binding_and_staleness(self):
        record = self.receipt()
        acceptance = Path(self.temp.name) / 'accept.json'
        # Without a versioned evidence schema nothing can bind, whatever it claims.
        acceptance.write_text(json.dumps({'passed': True, 'source_before': {'elsewhere/app.gopyt': 'ab'},
                                          'scenarios_passed': 3, 'scenarios_total': 3, 'failures': []}))
        report = obligation_report(self.root, [record], [acceptance], self.root.parent)
        self.assertEqual(report['evidence']['acceptance'][0]['status'], 'unbound')
        # Complete current coverage with consistent outcomes binds as passed.
        acceptance.write_text(json.dumps({'schema_version': 2, 'suite': 'app-suite', 'passed': True,
                                          'source_before': self._full_inventory(),
                                          'scenarios_passed': 3, 'scenarios_total': 3, 'failures': []}))
        report = obligation_report(self.root, [record], [acceptance], self.root.parent)
        self.assertEqual(report['evidence']['acceptance'][0]['status'], 'passed')
        self.edit('impl/app.gopyt', 'fee: state.fee', 'fee: state.fee + 0')
        report = obligation_report(self.root, [], [acceptance], self.root.parent)
        bound = report['evidence']['acceptance'][0]
        self.assertEqual(bound['status'], 'stale')
        self.assertIn('app/impl/app.gopyt', [row['file'] for row in bound['stale_files']])

    def test_impact_lists_changed_functions_obligations_and_stale_evidence(self):
        before = obligation_report(self.root, [self.receipt()], [], REPO)
        self.edit('impl/app.gopyt', 'fee: state.fee', 'fee: state.fee + 1')
        impact = change_impact(self.root, before, [], [], REPO)
        self.assertEqual(impact['changed_functions'], ['app.close'])
        self.assertIn('app.State.fee', impact['changed_fields'])
        self.assertIn('app.close', impact['affected_functions'])
        self.assertIn('test.app.closes_once', impact['affected_functions'])
        ids = {row['id'] for row in impact['obligations_applying']}
        self.assertEqual(ids, {'APP-SUM-001', 'APP-REJ-001', 'APP-FEE-001'})
        fee = next(row for row in impact['after']['obligations'] if row['id'] == 'APP-FEE-001')
        self.assertEqual(fee['scopes']['static'][0]['status'], 'not_established')
        self.assertTrue(any('no current passing test' in gap for gap in
                            next(r for r in impact['obligations_applying'] if r['id'] == 'APP-SUM-001')['gaps']))

    def test_facts_expose_field_coupling_and_body_identity(self):
        facts = compute_facts(self.root)
        close = facts['functions']['app.close']
        self.assertTrue(close['body_sha256'].startswith('sha256:'))
        writes = {(w['field'], w['source']) for w in close['writes'] if w['type'] == 'app.State'}
        self.assertIn(('fee', 'param_field:state.fee'), writes)
        self.assertIn(('status', 'enum:Status.Closed'), writes)
        compares = {(c['field'], c['variant']) for c in close['compares']}
        self.assertIn(('status', 'Status.Open'), compares)
        coupling = {(c['type'], c['field']): c for c in facts['field_coupling']}
        self.assertIn('app.close', coupling[('app.State', 'total')]['readers'])
        self.assertIn('app.initial', coupling[('app.State', 'total')]['writers'])

    def test_cli_refuses_existing_output(self):
        out = Path(self.temp.name) / 'report.json'
        self.assertEqual(main(['report', str(self.root), '--output', str(out)]), 0)
        self.assertEqual(main(['report', str(self.root), '--output', str(out)]), 1)

    def test_orders_registry_binds_completely(self):
        root = REPO / 'examples/orders'
        report = obligation_report(root, [], [], REPO)
        self.assertEqual(report['errors'], [], report['errors'])
        self.assertFalse(any(row['errors'] for row in report['obligations']))
        for row in report['obligations']:
            for item in row['scopes']['static']:
                self.assertIn(item['status'], ('bound', 'holds'), (row['id'], item))
            for item in row['scopes']['tests']:
                self.assertTrue(item['exists'], (row['id'], item['test']))
                self.assertTrue(item['reaches_obligated_functions'], (row['id'], item['test']))
        self.assertEqual(report['contract_document']['status'], 'current')
        ids = {row['id']: row['authority'] for row in report['obligations']}
        self.assertEqual(ids['POL-B-003'], 'proposed')
        self.assertEqual(ids['POL-C-001'], 'unresolved')


if __name__ == '__main__':
    unittest.main()


class ChallengeFindings(Obligations):
    """Regressions from benchmarks/obligations/challenge (independent challenge, 2026-09-06)."""

    def test_equivalent_clause_spelling_binds(self):
        reg = registry()
        reg['obligations'][0]['representations'][0]['text'] = '(not result.ok) or (result.state.total == (state.total + amount))'
        self.write_registry(reg)
        report = obligation_report(self.root, [], [], REPO)
        self.assertEqual(self.by_id(report)['APP-SUM-001']['scopes']['static'][0]['status'], 'bound')

    def test_local_alias_field_is_not_established_not_violated(self):
        self.edit('impl/app.gopyt', '        state: State {\n            status: Status.Closed\n            total: state.total + amount\n            fee: state.fee',
                  '        state: State {\n            status: Status.Closed\n            total: state.total + amount\n            fee: source.fee')
        self.edit('impl/app.gopyt', '    return Step {\n        ok: true', '    source = state\n    return Step {\n        ok: true')
        report = obligation_report(self.root, [], [], REPO)
        self.assertEqual(self.by_id(report)['APP-FEE-001']['scopes']['static'][0]['status'], 'not_established')

    def test_misspelled_preserved_field_is_a_gap(self):
        reg = registry()
        reg['obligations'][2]['representations'][0]['field'] = 'fe'
        self.write_registry(reg)
        row = self.by_id(obligation_report(self.root, [], [], REPO))['APP-FEE-001']
        self.assertEqual(row['scopes']['static'][0]['status'], 'no_constructors')
        self.assertTrue(any('no compiled constructor' in gap for gap in row['gaps']))

    def test_invalid_row_does_not_hide_other_obligations(self):
        reg = registry()
        reg['obligations'][4]['representations'] = [{'kind': 'test', 'test': 'test.app.closes_once'}]
        self.write_registry(reg)
        report = obligation_report(self.root, [], [], REPO)
        self.assertEqual(report['exit_code'], 1)
        self.assertEqual([e['id'] for e in report['errors']], ['APP-POL-001'])
        self.assertEqual(len(report['obligations']), 4)

    def test_halted_tests_are_labelled(self):
        self.edit('impl/app.gopyt', 'total: state.total + amount', 'total: state.total + amount + 1')
        record = create_receipt(self.root, 'test')
        report = obligation_report(self.root, [record], [], REPO)
        outcomes = {t['test']: t['outcome'] for row in report['obligations'] for t in row['scopes']['tests']}
        self.assertEqual(outcomes['test.app.closes_once'], 'failed')
        self.assertEqual(outcomes['test.app.mock_only'], 'not_run_after_earlier_trap')


class ReviewRegressions(Obligations):
    """Regressions for the four defects reproduced in
    docs/reviews/claude-obligations-2026-09-06/REVIEW.md. Each test failed
    against the reviewed version of gopyt/obligations.py."""

    def test_partial_or_contradictory_acceptance_is_never_passing(self):
        # Defect 1: one unchanged recorded file plus `passed: true` labeled an
        # incomplete, self-contradictory report as passing evidence.
        report_path = Path(self.temp.name) / 'report.json'
        report_path.write_text(json.dumps({'passed': True,
            'source_before': {'app/spec/app.gopyt': digest((self.root / 'spec/app.gopyt').read_bytes())},
            'scenarios_passed': 0, 'scenarios_total': 100, 'failures': ['failed']}))
        self.edit('impl/app.gopyt', 'total: state.total + amount', 'total: state.total + amount + 1')
        report = obligation_report(self.root, [], [report_path], self.root.parent)
        bound = report['evidence']['acceptance'][0]
        self.assertNotEqual(bound['status'], 'passed')
        self.assertEqual(bound['status'], 'unbound')  # no versioned evidence schema at all

        # A version nobody defined binds nothing either.
        report_path.write_text(json.dumps({'schema_version': 99, 'suite': 'demo', 'passed': True,
            'source_before': self._full_inventory(),
            'scenarios_passed': 3, 'scenarios_total': 3, 'failures': []}))
        self.assertEqual(obligation_report(self.root, [], [report_path], self.root.parent)
                         ['evidence']['acceptance'][0]['status'], 'unbound')

        report_path.write_text(json.dumps({'schema_version': 2, 'suite': 'demo', 'passed': True,
            'source_before': {'app/spec/app.gopyt': digest((self.root / 'spec/app.gopyt').read_bytes())},
            'scenarios_passed': 100, 'scenarios_total': 100, 'failures': []}))
        report = obligation_report(self.root, [], [report_path], self.root.parent)
        bound = report['evidence']['acceptance'][0]
        self.assertEqual(bound['status'], 'partial')
        self.assertIn('app/impl/app.gopyt', bound['missing_package_files'])
        self.assertTrue(bound['missing_engine_files'])

        report_path.write_text(json.dumps({'schema_version': 2, 'suite': 'demo', 'passed': True,
            'source_before': self._full_inventory(),
            'scenarios_passed': 0, 'scenarios_total': 100, 'failures': ['failed']}))
        report = obligation_report(self.root, [], [report_path], self.root.parent)
        bound = report['evidence']['acceptance'][0]
        self.assertEqual(bound['status'], 'contradictory')

    def test_acceptance_representation_binds_by_suite_not_basename(self):
        # Defect 1: representations associated reports by basename, so any
        # report.json from any suite satisfied any acceptance obligation.
        report_path = Path(self.temp.name) / 'report.json'
        report_path.write_text(json.dumps({'schema_version': 2, 'suite': 'demo', 'passed': True,
            'source_before': self._full_inventory(),
            'scenarios_passed': 3, 'scenarios_total': 3, 'failures': []}))
        reg = registry()
        reg['obligations'][3]['representations'] = [{'kind': 'acceptance', 'suite': 'other-suite',
                                                     'report': 'report.json'}]
        self.write_registry(reg)
        report = obligation_report(self.root, [], [report_path], self.root.parent)
        row = self.by_id(report)['APP-DOC-001']
        self.assertEqual(row['scopes']['acceptance'][0]['bound'][0]['status'], 'not_supplied')
        self.assertTrue(any('not_supplied' in gap for gap in row['gaps']))
        reg['obligations'][3]['representations'] = [{'kind': 'acceptance', 'suite': 'demo',
                                                     'report': 'report.json'}]
        self.write_registry(reg)
        report = obligation_report(self.root, [], [report_path], self.root.parent)
        row = self.by_id(report)['APP-DOC-001']
        self.assertEqual(row['scopes']['acceptance'][0]['bound'][0]['status'], 'passed')
        self.assertEqual(row['gaps'], [])

    def test_uncalled_function_is_not_reported_as_executed(self):
        # Defect 2: a call guarded by `if false` was listed as execution
        # evidence for the runtime contract.
        (self.root / 'test/app.gopyt').write_text('''module test.app

use app { close, initial }

test closes_once
{
    if false {
        ignored = app.close(app.initial(5), 7)
    }
    return unit
}
''')
        write_lock(str(self.root))
        report = obligation_report(self.root, [self.receipt()], [], REPO)
        row = self.by_id(report)['APP-SUM-001']
        runtime = row['scopes']['runtime_contract'][0]
        self.assertEqual(runtime['reached_by_passing_tests'], ['test.app.closes_once'])
        self.assertNotIn('executed_by_passing_tests', json.dumps(runtime))
        self.assertIn('static call closure', runtime['reach_basis'])
        self.assertIn('not instrumented', runtime['reach_basis'])

    def test_arithmetic_change_pulls_in_downstream_readers(self):
        # Defect 3: both write sources classified `computed`, so a changed
        # arithmetic expression dropped the field, its reader and its obligation.
        for name, body in [('spec/app.gopyt', '\nfn read_total(state: State) -> i64\n'),
                           ('impl/app.gopyt', '\nfn read_total(state: State) -> i64\n{\n    return state.total\n}\n')]:
            path = self.root / name
            path.write_text(path.read_text() + body)
        write_lock(str(self.root))
        reg = registry()
        reg['obligations'].append({'id': 'APP-READ-001', 'title': 'read total', 'authority': 'contract',
                                   'functions': ['app.read_total'], 'state_fields': ['total'],
                                   'representations': [{'kind': 'prose', 'text': 'total must be correct'}]})
        self.write_registry(reg)
        before = obligation_report(self.root, [], [], REPO)
        self.edit('impl/app.gopyt', 'total: state.total + amount', 'total: state.total + amount + 1')
        impact = change_impact(self.root, before, [], [], REPO)
        self.assertIn('app.State.total', impact['changed_fields'])
        self.assertIn('app.read_total', impact['affected_functions'])
        self.assertIn('app.read_total', impact['affected_via_field_coupling'])
        ids = {row['id'] for row in impact['obligations_applying']}
        self.assertIn('APP-READ-001', ids)

    def test_stale_contract_marks_every_obligation_applicability(self):
        # Defect 4: a changed contract document left obligation gaps empty and
        # stale_evidence silent while test facts stayed current.
        receipt = self.receipt()
        (self.root / 'CONTRACT.md').write_text('changed policy\n')
        report = obligation_report(self.root, [receipt], [], REPO)
        self.assertEqual(report['contract_document']['status'], 'stale')
        self.assertIn('contract_document', [row.get('kind') for row in report['stale_evidence']])
        for row in report['obligations']:
            self.assertEqual(row['applicability'], 'stale', row['id'])
            self.assertIn('applicability stale', row['gaps'][0])
        # Execution facts are preserved, not erased: the receipt is still current.
        self.assertEqual(report['evidence']['test_receipts'][0]['status'], 'current')
        self.assertEqual(self.by_id(report)['APP-SUM-001']['scopes']['tests'][0]['outcome'], 'passed')

    def test_impact_propagates_contract_staleness(self):
        receipt = self.receipt()
        before = obligation_report(self.root, [receipt], [], REPO)
        (self.root / 'CONTRACT.md').write_text('changed policy\n')
        impact = change_impact(self.root, before, [receipt], [], REPO)
        self.assertTrue(any(row.get('kind') == 'contract_document' for row in impact['stale_evidence']))

    def test_complete_report_binds_when_repo_root_is_not_package_parent(self):
        # Independent-review finding: coverage compared root.parent-relative keys
        # against repo_root-relative inventory keys, so a genuinely complete
        # report could never bind outside the unit-test layout.
        nested = Path(self.temp.name) / 'pkg' / 'app'
        nested.mkdir(parents=True)
        write_pkg(str(nested), {'spec/app.gopyt': SPEC, 'impl/app.gopyt': IMPL_OK,
                                'test/app.gopyt': TEST}, fmt=True)
        (nested / 'CONTRACT.md').write_bytes(b'rules\n')
        (nested / 'obligations.json').write_text((self.root / 'obligations.json').read_text())
        write_lock(str(nested))
        current = create_receipt(nested, 'context')
        files = _current_package_files(nested, current, Path(self.temp.name))
        self.assertTrue(all(key.startswith('pkg/app/') for key in files), sorted(files))
        files.update({'gopyt/' + name: value for name, value in current['engine']['files'].items()})
        report_path = Path(self.temp.name) / 'nested-report.json'
        report_path.write_text(json.dumps({'schema_version': 2, 'suite': 'demo', 'passed': True,
                                           'source_before': files,
                                           'scenarios_passed': 3, 'scenarios_total': 3, 'failures': []}))
        report = obligation_report(nested, [], [report_path], Path(self.temp.name))
        bound = report['evidence']['acceptance'][0]
        self.assertEqual(bound['status'], 'passed', bound)

    def test_boolean_scenario_counts_are_contradictory(self):
        # bool is an int subclass; True/True must not satisfy the count check.
        report_path = Path(self.temp.name) / 'report.json'
        report_path.write_text(json.dumps({'schema_version': 2, 'suite': 'demo', 'passed': True,
                                           'source_before': self._full_inventory(),
                                           'scenarios_passed': True, 'scenarios_total': True, 'failures': []}))
        report = obligation_report(self.root, [], [report_path], self.root.parent)
        self.assertEqual(report['evidence']['acceptance'][0]['status'], 'contradictory')

    def test_decoy_gopyt_directory_does_not_cover_the_engine(self):
        full = self._full_inventory()
        inventory = {key: value for key, value in full.items() if not key.startswith('gopyt/')}
        # A nonexistent decoy path claims nothing...
        inventory['third_party/gopyt/check.py'] = full['gopyt/check.py']
        report_path = Path(self.temp.name) / 'report.json'
        report_path.write_text(json.dumps({'schema_version': 2, 'suite': 'demo', 'passed': True,
                                           'source_before': inventory,
                                           'scenarios_passed': 3, 'scenarios_total': 3, 'failures': []}))
        report = obligation_report(self.root, [], [report_path], self.root.parent)
        bound = report['evidence']['acceptance'][0]
        self.assertNotEqual(bound['status'], 'passed')
        self.assertTrue(bound['missing_engine_files'])
        # ...and neither does a real byte copy of the engine file under a
        # directory that merely happens to be named gopyt (review re-check).
        decoy = Path(self.temp.name) / 'third_party' / 'gopyt'
        decoy.mkdir(parents=True)
        shutil.copyfile(REPO / 'gopyt' / 'check.py', decoy / 'check.py')
        report = obligation_report(self.root, [], [report_path], self.root.parent)
        bound = report['evidence']['acceptance'][0]
        self.assertEqual(bound['status'], 'partial')
        self.assertTrue(bound['missing_engine_files'])
        self.assertNotIn('third_party/gopyt/check.py',
                         [row['file'] for row in bound['stale_files']])

    def test_unbound_or_missing_contract_flags_contract_authority(self):
        reg = registry()
        del reg['contract']
        self.write_registry(reg)
        report = obligation_report(self.root, [], [], REPO)
        self.assertEqual(report['contract_document']['status'], 'unbound')
        row = self.by_id(report)['APP-SUM-001']
        self.assertEqual(row['applicability'], 'unbound')
        self.assertIn('contract authority unverified', row['gaps'][0])
        # Only contract-authority rows claim the document; proposed/unresolved do not.
        self.assertEqual(self.by_id(report)['APP-POL-001']['applicability'], 'current')
        reg = registry()
        reg['contract']['path'] = 'GONE.md'
        self.write_registry(reg)
        report = obligation_report(self.root, [], [], REPO)
        self.assertEqual(report['contract_document']['status'], 'missing')
        row = self.by_id(report)['APP-SUM-001']
        self.assertEqual(row['applicability'], 'unbound')
        self.assertIn('GONE.md', row['gaps'][0])

    def test_duplicate_json_keys_make_the_report_invalid(self):
        full = self._full_inventory()
        raw = json.dumps({'schema_version': 2, 'suite': 'demo', 'passed': True,
                          'source_before': full, 'scenarios_passed': 3,
                          'scenarios_total': 3, 'failures': []})
        report_path = Path(self.temp.name) / 'report.json'
        report_path.write_text(raw[:-1] + ', "passed": false}')
        report = obligation_report(self.root, [], [report_path], self.root.parent)
        self.assertEqual(report['evidence']['acceptance'][0]['status'], 'invalid')


class ContractNamePropagation(unittest.TestCase):
    """A spec `use` satisfied only by a contract must count as used even when an
    earlier impl body already used the same name (challenge finding 14)."""

    def test_helper_sorting_before_contract_owner(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / 'app'
        root.mkdir()
        spec = ('module app\n\nenum Flag {\n    On\n    Off\n}\n\ntype Out {\n    flag: Flag\n}\n\n'
                'fn zed(amount: i64) -> Out\n    ensures result.flag == Flag.On or amount < 0\n')
        impl = ('module app\n\nfn alpha(amount: i64) -> Out\n{\n    return Out {\n        flag: Flag.On\n    }\n}\n\n'
                'fn zed(amount: i64) -> Out\n    ensures result.flag == Flag.On or amount < 0\n{\n    return alpha(amount)\n}\n')
        write_pkg(str(root), {'spec/app.gopyt': spec, 'impl/app.gopyt': impl}, fmt=True)
        record = create_receipt(root, 'context')
        self.assertEqual(record['compile']['status'], 'passed', record['compile'])


class Challenge2026(Obligations):
    """Counterexamples from benchmarks/obligations/challenge-2026-09-06 (runs 01-08
    there are the preserved pre-repair reproducers). Each test fails on the
    defective engine and pins the repaired behavior."""

    def _rehash(self, record):
        unsigned = dict(record)
        unsigned.pop('receipt_sha256', None)
        record['receipt_sha256'] = digest(canonical(unsigned))
        return record

    def test_contradictory_current_receipts_are_flagged_not_order_decided(self):
        genuine = self.receipt()
        flipped = json.loads(json.dumps(genuine))
        flipped['tests']['cases'][0]['status'] = 'failed'
        flipped['tests']['cases'][0]['trap'] = 13
        flipped['tests']['status'] = 'failed'
        flipped['exit_code'] = 2
        self._rehash(flipped)
        forward = obligation_report(self.root, [genuine, flipped], [], REPO)
        backward = obligation_report(self.root, [flipped, genuine], [], REPO)
        forward_row = self.by_id(forward)['APP-SUM-001']['scopes']['tests'][0]
        backward_row = self.by_id(backward)['APP-SUM-001']['scopes']['tests'][0]
        self.assertEqual(forward_row['outcome'], backward_row['outcome'])
        self.assertEqual(forward_row['outcome'], 'contradictory')
        gaps = ' '.join(self.by_id(forward)['APP-SUM-001']['gaps'])
        self.assertIn('contradict', gaps)

    def _acceptance_file(self, name, total, passed, suite='demo-suite'):
        current = create_receipt(self.root, 'context')
        repo_root = self.root.parent
        files = dict(_current_package_files(self.root, current, repo_root))
        files.update({'gopyt/' + key: value for key, value in current['engine']['files'].items()})
        path = Path(self.temp.name) / name
        path.write_text(json.dumps({'schema_version': 2, 'suite': suite, 'passed': passed == total,
                                    'source_before': files, 'scenarios_passed': passed,
                                    'scenarios_total': total, 'failures': []}))
        return path

    def test_acceptance_summary_discloses_corpus_size(self):
        trivial = self._acceptance_file('trivial.json', 1, 1)
        reg = registry()
        reg['obligations'].append({'id': 'APP-ACC-001', 'title': 'suite passes', 'authority': 'contract',
                                   'functions': [], 'state_fields': [],
                                   'representations': [{'kind': 'acceptance', 'suite': 'demo-suite'}]})
        self.write_registry(reg)
        report = obligation_report(self.root, [], [trivial], self.root.parent)
        row = self.by_id(report)['APP-ACC-001']
        self.assertIn('passed', row['scopes']['acceptance'][0]['bound'][0]['status'])
        self.assertIn('1 scenario)', row['summary'])
        # The same file supplied twice is one report, not an ambiguity (review note 1).
        twice = obligation_report(self.root, [], [trivial, trivial], self.root.parent)
        row2 = self.by_id(twice)['APP-ACC-001']
        self.assertFalse(row2['scopes']['acceptance'][0]['ambiguous'])
        self.assertEqual(len(row2['scopes']['acceptance'][0]['bound']), 1)

    def test_one_suite_identity_two_corpora_is_flagged(self):
        trivial = self._acceptance_file('trivial.json', 1, 1)
        full = self._acceptance_file('full.json', 3186, 3186)
        reg = registry()
        reg['obligations'].append({'id': 'APP-ACC-001', 'title': 'suite passes', 'authority': 'contract',
                                   'functions': [], 'state_fields': [],
                                   'representations': [{'kind': 'acceptance', 'suite': 'demo-suite'}]})
        self.write_registry(reg)
        report = obligation_report(self.root, [], [trivial, full], self.root.parent)
        gaps = ' '.join(self.by_id(report)['APP-ACC-001']['gaps'])
        self.assertIn('binds 2 distinct reports', gaps)

    def test_impact_records_engine_change_between_reports(self):
        before = obligation_report(self.root, [self.receipt()], [], REPO)
        before = json.loads(json.dumps(before))
        before['current']['engine']['sha256'] = 'sha256:' + '0' * 64
        impact = change_impact(self.root, before, [], [], REPO)
        reasons = ' '.join(str(item.get('reason', '')) for item in impact['unknowns'])
        self.assertIn('engine', reasons)
        self.assertNotIn('nothing is stale', reasons)

    def test_impact_flags_before_report_from_another_package(self):
        other = Path(self.temp.name) / 'other'
        other.mkdir()
        write_pkg(str(other), {'spec/app.gopyt': SPEC, 'impl/app.gopyt': IMPL_OK}, fmt=True)
        (other / 'CONTRACT.md').write_bytes(b'rules\n')
        (other / 'obligations.json').write_text(json.dumps(registry()))
        write_lock(str(other))
        before = obligation_report(other, [], [], REPO)
        impact = change_impact(self.root, before, [], [], REPO)
        reasons = ' '.join(str(item.get('reason', '')) for item in impact['unknowns'])
        self.assertIn('different package', reasons)

    def test_impact_records_registry_change_between_reports(self):
        before = obligation_report(self.root, [self.receipt()], [], REPO)
        reg = registry()
        reg['obligations'][0]['representations'] = [{'kind': 'prose', 'text': 'review only'}]
        self.write_registry(reg)
        impact = change_impact(self.root, before, [], [], REPO)
        self.assertTrue(impact.get('registry_changed'))
        reasons = ' '.join(str(item.get('reason', '')) for item in impact['unknowns'])
        self.assertIn('registry changed', reasons)
        self.assertNotIn('nothing is stale', reasons)

    def test_uninstantiated_generic_clause_is_not_claimed_compiled(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / 'app'
        root.mkdir()
        spec = ('module app\n\nfn cap(value: i64, ceiling_value: i64) -> i64\n'
                '    ensures value <= ceiling_value or result == ceiling_value\n\n'
                'fn count[T](items: list[T]) -> i64\n    ensures result >= 0\n')
        impl = ('module app\n\nuse core.list { len }\n\n'
                'fn cap(value: i64, ceiling_value: i64) -> i64\n'
                '    ensures value <= ceiling_value or result == ceiling_value\n'
                '{\n    if value > ceiling_value {\n        return ceiling_value\n    }\n    return value\n}\n\n'
                'fn count[T](items: list[T]) -> i64\n    ensures result >= 0\n'
                '{\n    return core.list.len[T](items)\n}\n')
        write_pkg(str(root), {'spec/app.gopyt': spec, 'impl/app.gopyt': impl}, fmt=True)
        (root / 'CONTRACT.md').write_bytes(b'rules\n')
        reg = registry()
        reg['obligations'] = [{'id': 'APP-GEN-001', 'title': 'count is non-negative', 'authority': 'contract',
                               'functions': ['app.count'], 'state_fields': [],
                               'representations': [{'kind': 'ensures', 'function': 'app.count',
                                                    'text': 'result >= 0'}]}]
        (root / 'obligations.json').write_text(json.dumps(reg))
        write_lock(str(root))
        report = obligation_report(root, [], [], REPO)
        row = self.by_id(report)['APP-GEN-001']
        self.assertEqual(row['scopes']['static'][0]['status'], 'bound')
        self.assertIn('no instantiation', ' '.join(row['gaps']))
        self.assertNotIn('typechecked and compiled', row['summary'])
        self.assertFalse(row['scopes']['runtime_contract'])

    def test_stale_except_names_are_flagged(self):
        reg = registry()
        reg['obligations'][2]['representations'] = [
            {'kind': 'preserved_field', 'type': 'app.State', 'field': 'fee',
             'except': ['app.initial', 'app.ghost', 'vanished.name']}]
        self.write_registry(reg)
        report = obligation_report(self.root, [], [], REPO)
        row = self.by_id(report)['APP-FEE-001']
        self.assertEqual(row['scopes']['static'][0]['unknown_except'], ['app.ghost', 'vanished.name'])
        gaps = ' '.join(row['gaps'])
        self.assertIn('stale exemption list', gaps)
