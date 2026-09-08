#!/usr/bin/env python3
"""Independently reconcile retained quotation-v1 pilot evidence; never run subjects."""
from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys

LANGUAGES = ('gopyt', 'python', 'typescript')
TASKS = ('a', 'b', 'c')
IGNORED = {'build', '__pycache__', '.mypy_cache', '.gopyt-state'}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate object key')
        result[key] = value
    return result


def inventory(root):
    result = {}
    for parent, directories, files in os.walk(root, followlinks=False):
        for name in list(directories):
            path = Path(parent, name)
            if name in IGNORED:
                directories.remove(name)
            elif path.is_symlink():
                result[str(path.relative_to(root))] = 'symlink:' + os.readlink(path)
                directories.remove(name)
        for name in files:
            if name == '.gopyt-transaction.lock':
                continue
            path = Path(parent, name)
            result[str(path.relative_to(root))] = ('symlink:' + os.readlink(path) if path.is_symlink()
                                                 else hashlib.sha256(path.read_bytes()).hexdigest())
    return result


def event_metrics(path):
    events, malformed, items, usage, threads = [], [], {}, [], []
    terminal = None
    for line in path.read_bytes().splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError('not an object')
        except ValueError:
            malformed.append(line.decode(errors='replace'))
            continue
        events.append(event)
        item = event.get('item')
        if isinstance(item, dict) and item.get('type') not in (None, 'agent_message', 'reasoning', 'todo_list', 'error'):
            identity = item.get('id')
            if not isinstance(identity, str):
                identity = f'missing-id-event-{len(events)}'
            items[identity] = item['type']
        if event.get('type') in ('turn.completed', 'turn.failed'):
            terminal = event['type']
            usage.append({'type': terminal, 'usage': event.get('usage')})
        if event.get('type') == 'thread.started' and 'thread_id' in event:
            threads.append(event['thread_id'])
    return {'usage': usage[-1]['usage'] if usage else None, 'usage_events': usage,
            'terminal_event': terminal, 'observed_tool_items': len(items),
            'observed_item_types': items, 'thread_ids': threads, 'malformed_event_lines': malformed}


def command_pass(command):
    return type(command.get('returncode')) is int and command['returncode'] == 0 and command.get('timed_out') is not True


def nonnegative_number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


class Audit:
    def __init__(self, campaign):
        self.campaign = campaign.resolve()
        self.errors, self.pending, self.warnings = [], [], []
        self.rows = []
        self.thread_ids = []
        self.corpora = {}

    def require(self, condition, message):
        if not condition:
            self.errors.append(message)

    def read(self, relative, pending=False):
        path = self.campaign / relative
        if not path.is_file():
            (self.pending if pending else self.errors).append(f'missing {relative}')
            return None
        try:
            return json.loads(path.read_text())
        except (ValueError, OSError) as error:
            self.errors.append(f'invalid {relative}: {error}')
            return None

    def check_commands(self, evaluation, label, commands):
        for kind in ('static', 'public', 'native', 'oracle_process'):
            value = evaluation.get(kind)
            if kind == 'native' and 'native' not in commands:
                self.require(value is None, f'{label}: unexpected native gate')
                continue
            self.require(isinstance(value, dict), f'{label}: missing {kind} command record')
            if not isinstance(value, dict):
                continue
            self.require(value.get('pass') is command_pass(value), f'{label}: {kind} pass disagrees with exit/timeout')
            if kind in commands:
                self.require(value.get('command') == commands[kind], f'{label}: {kind} command changed')
            if value.get('elapsed_seconds') is not None:
                self.require(nonnegative_number(value['elapsed_seconds']), f'{label}: invalid {kind} elapsed time')
        process = evaluation.get('oracle_process') or {}
        behavior = evaluation.get('behavior') or {}
        try:
            raw_behavior = json.loads(process.get('stdout', ''))
        except ValueError:
            raw_behavior = None
        if raw_behavior is not None:
            self.require(raw_behavior == behavior, f'{label}: behavior differs from oracle stdout')
        else:
            self.require(behavior.get('business_pass') is False, f'{label}: unreadable oracle output claimed successful')
        if behavior.get('schema') == 'quotation-v1-business-report' and 'passed' not in behavior:
            # A launch failure, output-size limit or undecodable output can end
            # evaluation before per-case scoring. Retain that honest failure.
            self.require(behavior.get('business_pass') is False and bool(behavior.get('errors')),
                         f'{label}: incomplete oracle report lacks explicit failure')
            return
        if behavior.get('schema') == 'quotation-v1-business-report':
            groups = behavior.get('groups', {})
            total = sum(group['total'] for group in groups.values())
            passed = sum(group['passed'] for group in groups.values())
            self.require(all(type(group['passed']) is int and type(group['total']) is int
                             and 0 <= group['passed'] <= group['total'] for group in groups.values()),
                         f'{label}: invalid oracle group counts')
            self.require(total == behavior.get('case_count') and passed == behavior.get('passed'),
                         f'{label}: oracle case counts disagree')
            self.require(len(behavior.get('failures', [])) == total - passed, f'{label}: oracle failure count disagrees')
            correct = not behavior.get('errors') and passed == total
            self.require(behavior.get('business_pass') is correct, f'{label}: oracle business gate disagrees')
            self.require(behavior.get('command') == commands['adapter'], f'{label}: oracle adapter command changed')
            regression = all(group['passed'] == group['total'] for name, group in groups.items() if name == 'regression')
            self.require(behavior.get('regression_pass') is regression, f'{label}: regression gate disagrees')

    def check_trial(self, trial, manifest, reviews):
        name = trial['id']
        prefix = Path('trials', name)
        result = self.read(prefix / 'result.json', pending=True)
        if result is None:
            return None
        self.require(result.get('trial') == trial, f'{name}: trial identity changed')
        if result.get('infrastructure_error'):
            self.require(result.get('mechanical_pass') is False, f'{name}: infrastructure failure claims mechanical success')
        session = result.get('session') or {}
        evaluation = result.get('evaluation') or {}
        if session:
            stored_session = self.read(prefix / 'session.json')
            self.require(stored_session == session, f'{name}: session result differs from retained session')
            path = self.campaign / prefix / 'events.jsonl'
            if path.is_file():
                derived = event_metrics(path)
                for key, value in derived.items():
                    if session.get('launch_error') and key in ('observed_item_types', 'thread_ids', 'malformed_event_lines'):
                        continue
                    self.require(session.get(key) == value, f'{name}: event-derived {key} differs')
            else:
                self.errors.append(f'{name}: missing event stream')
            self.require(nonnegative_number(session.get('wall_seconds')), f'{name}: invalid wall time')
            self.thread_ids.extend(session.get('thread_ids', []))
            usage = session.get('usage')
            if isinstance(usage, dict):
                for key, value in usage.items():
                    if key.endswith('_tokens'):
                        self.require(type(value) is int and value >= 0, f'{name}: invalid token count {key}')
                if 'cached_input_tokens' in usage and 'input_tokens' in usage:
                    self.require(usage['cached_input_tokens'] <= usage['input_tokens'], f'{name}: cached input exceeds input')
            elif usage is not None:
                self.errors.append(f'{name}: usage is neither object nor null')
            budget = session.get('budget_event')
            self.require(session.get('censored') is (budget is not None), f'{name}: censoring disagrees with budget event')
            if session.get('observed_tool_items', 0) > manifest['observed_tool_limit']:
                self.require(budget is not None, f'{name}: exceeded tool cap without budget event')
            if budget:
                self.require(budget.get('reason') in ('wall_deadline', 'observed_tool_limit'), f'{name}: unknown budget reason')
                if budget.get('reason') == 'wall_deadline':
                    self.require(budget.get('elapsed_seconds', -1) >= manifest['wall_limit_seconds'], f'{name}: premature wall-deadline claim')
                else:
                    self.require(budget.get('observed', 0) > manifest['observed_tool_limit'], f'{name}: premature tool-cap claim')
            invocation = self.read(prefix / 'invocation.json') or {}
            command = invocation.get('command', [])
            for flag in ('--ephemeral', '--ignore-user-config', '--ignore-rules', '--json', '--sandbox'):
                self.require(flag in command, f'{name}: missing invocation flag {flag}')
            self.require('resume' not in command, f'{name}: resumed agent session')
            self.require('-m' in command and command[command.index('-m') + 1] == manifest['model'], f'{name}: model differs')
            self.require('model_reasoning_effort=' + json.dumps(manifest['reasoning_effort']) in command, f'{name}: reasoning config differs')
            expected_prompt = (self.campaign / 'prompts' / (name + '.txt')).read_text()
            self.require(command and command[-1] == expected_prompt, f'{name}: prompt differs')
            self.require(invocation.get('cwd') == str(self.campaign / prefix / 'work'), f'{name}: invocation workspace differs')
        if evaluation:
            self.check_commands(evaluation, name, manifest['commands'][trial['language']])
            behavior = evaluation.get('behavior') or {}
            if behavior.get('schema') == 'quotation-v1-business-report':
                self.require(behavior.get('task') == trial['task'] and behavior.get('seed') == manifest['seed'],
                             f'{name}: oracle task or seed changed')
                corpus_key = 'baseline' if trial['task'] == 'c' else trial['task']
                identity = (behavior.get('case_count'), behavior.get('corpus_sha256'))
                self.require(identity == self.corpora.setdefault(corpus_key, identity), f'{name}: oracle corpus differs within task')
            before = self.read(prefix / 'initial-inventory.json') or {}
            after = self.read(prefix / 'final-inventory.json') or {}
            self.require(before == inventory(self.campaign / 'baseline' / trial['language']), f'{name}: initial subject differs from baseline')
            for folder, record in (('evaluation-work', 'post-evaluation-inventory.json'),
                                   ('submitted', 'submitted-post-evaluation-inventory.json'),
                                   ('work', 'work-post-evaluation-inventory.json')):
                actual = inventory(self.campaign / prefix / folder)
                stored = self.read(prefix / record)
                self.require(actual == stored, f'{name}: {folder} no longer matches retained inventory')
            post = self.read(prefix / 'post-evaluation-inventory.json') or {}
            changed = sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
            allowed = {'clarification.json'}
            if trial['task'] != 'c':
                allowed.update(manifest['allowlists'][trial['language']])
            unauthorized = [key for key in changed if key not in allowed]
            symlinks = [key for key, value in after.items() if value.startswith('symlink:')]
            mutations = sorted(key for key in set(after) | set(post) if after.get(key) != post.get(key))
            snapshots = (inventory(self.campaign / prefix / 'submitted') == after
                         and inventory(self.campaign / prefix / 'work') == after)
            if snapshots:
                lines = []
                for relative in changed:
                    old_path = self.campaign / 'baseline' / trial['language'] / relative
                    new_path = self.campaign / prefix / 'submitted' / relative
                    old = old_path.read_text(errors='replace').splitlines(keepends=True) if old_path.is_file() else []
                    new = new_path.read_text(errors='replace').splitlines(keepends=True) if new_path.is_file() and not new_path.is_symlink() else []
                    lines.extend(difflib.unified_diff(old, new, fromfile='before/' + relative, tofile='after/' + relative))
                diff_path = self.campaign / prefix / 'source.diff'
                self.require(diff_path.is_file() and diff_path.read_text() == ''.join(lines), f'{name}: retained source diff differs')
            integrity = result.get('integrity') or {}
            expected_integrity = not unauthorized and not symlinks and not mutations and snapshots
            for key, expected in {'changed_files': changed, 'unauthorized_files': unauthorized,
                                  'symlinks': symlinks, 'evaluation_mutations': mutations,
                                  'submitted_and_work_preserved': snapshots, 'mechanical_pass': expected_integrity}.items():
                self.require(integrity.get(key) == expected, f'{name}: integrity {key} differs')
            gate = expected_integrity
            for key in ('static', 'public', 'oracle_process'):
                gate = gate and command_pass(evaluation.get(key) or {})
            if 'native' in manifest['commands'][trial['language']]:
                gate = gate and command_pass(evaluation.get('native') or {})
            gate = (gate and evaluation.get('behavior', {}).get('business_pass') is True
                    and session.get('budget_event') is None and session.get('exit_code') == 0
                    and session.get('terminal_event') == 'turn.completed'
                    and not session.get('process_error') and not session.get('malformed_event_lines'))
            if trial['task'] == 'c':
                clarification = evaluation.get('clarification') or {}
                path = self.campaign / prefix / 'submitted/clarification.json'
                try:
                    if path.stat().st_size > 65536:
                        raise ValueError('clarification artifact exceeds limit')
                    content = json.loads(path.read_text(), object_pairs_hook=unique_object)
                    structural = (type(content) is dict and set(content) == {'status', 'questions', 'assumptions'}
                                  and content['status'] == 'needs_clarification'
                                  and type(content['questions']) is list and bool(content['questions'])
                                  and all(type(question) is str and bool(question.strip()) for question in content['questions'])
                                  and content['assumptions'] == [])
                except (ValueError, OSError):
                    structural = False
                self.require(clarification.get('structure_pass') is structural, f'{name}: clarification structure differs')
                gate = gate and structural
            self.require(result.get('mechanical_pass') is bool(gate), f'{name}: mechanical gate does not follow evidence')
        elif not result.get('infrastructure_error'):
            self.errors.append(f'{name}: missing evaluation without infrastructure disposition')
        review = reviews.get(name, {})
        keys = ['source_integrity_pass', 'access_review_pass']
        if trial['task'] == 'c':
            keys.append('clarification_semantic_pass')
        for key in keys:
            if key not in review:
                self.pending.append(f'{name}: missing review {key}')
            else:
                self.require(type(review[key]) is bool, f'{name}: review {key} must be boolean')
        usage = session.get('usage') or {}
        behavior = evaluation.get('behavior') or {}
        return {**trial, 'disposition': 'infrastructure_error' if result.get('infrastructure_error') else 'recorded',
                'static_pass': evaluation.get('static', {}).get('pass'), 'public_pass': evaluation.get('public', {}).get('pass'),
                'business_pass': behavior.get('business_pass'), 'business_cases': behavior.get('case_count', 0),
                'business_cases_passed': sum(group['passed'] for group in behavior.get('groups', {}).values()),
                'mechanical_pass': result.get('mechanical_pass'),
                'source_integrity_pass': review.get('source_integrity_pass'), 'access_review_pass': review.get('access_review_pass'),
                'clarification_semantic_pass': review.get('clarification_semantic_pass'),
                'reviews_complete': all(key in review and review[key] is not None for key in keys),
                'final_pass': result.get('mechanical_pass') is True and all(review.get(key) is True for key in keys),
                'wall_seconds': session.get('wall_seconds'), 'observed_tool_items': session.get('observed_tool_items'),
                'input_tokens': usage.get('input_tokens'), 'cached_input_tokens': usage.get('cached_input_tokens'),
                'output_tokens': usage.get('output_tokens'), 'budget_event': session.get('budget_event')}

    def run(self):
        manifest = self.read(Path('manifest.json'))
        if not isinstance(manifest, dict):
            return self.report()
        schedule = manifest.get('schedule', [])
        expected = {(language, task, repeat) for language in LANGUAGES for task in TASKS for repeat in (1, 2)}
        cells = [(trial.get('language'), trial.get('task'), trial.get('repeat')) for trial in schedule]
        ids = [trial['id'] for trial in schedule]
        self.require(len(schedule) == 18 and len(set(ids)) == 18 and set(cells) == expected, 'schedule is not the declared 18 unique cells')
        actual = inventory(self.campaign)
        frozen = manifest.get('frozen_files', {})
        for name, digest in frozen.items():
            self.require(actual.get(name) == digest, f'frozen file changed: {name}')
        for prefix in ('support/', 'baseline/', 'prompts/'):
            self.require({k: v for k, v in actual.items() if k.startswith(prefix)} ==
                         {k: v for k, v in frozen.items() if k.startswith(prefix)}, f'frozen subtree inventory changed: {prefix}')
        baseline = self.read(Path('baseline-pass.json')) or {}
        self.require(baseline.get('pass') is True and set(baseline.get('languages', [])) == set(LANGUAGES), 'baseline-pass gate is incomplete')
        for language in LANGUAGES:
            record = self.read(Path('baseline-check', language + '.json'))
            if record:
                self.check_commands(record, 'baseline/' + language, manifest['commands'][language])
                self.require(inventory(self.campaign / 'baseline-check' / language) == inventory(self.campaign / 'baseline' / language),
                             f'{language}: baseline validation source changed')
                identity = (record['behavior'].get('case_count'), record['behavior'].get('corpus_sha256'))
                self.require(identity == self.corpora.setdefault('baseline', identity), f'{language}: baseline oracle corpus differs')
                self.require(record.get('source_preserved') is True and record.get('behavior', {}).get('business_pass') is True,
                             f'{language}: baseline source or business gate failed')
        reviews = {}
        for path in sorted((self.campaign / 'reviews').glob('*.json')):
            document = self.read(path.relative_to(self.campaign)) or {}
            for review in document.get('trials', []):
                name = review.get('id')
                self.require(name in ids, f'unknown reviewed trial {name}')
                self.require(name not in reviews, f'duplicate review for {name}')
                reviews[name] = review
                if not review.get('rationale'):
                    self.warnings.append(f'{name}: review has no common rationale field; inspect dimension-specific evidence')
                for relative, digest in review.get('evidence_sha256', {}).items():
                    path = self.campaign / 'trials' / str(name) / relative
                    self.require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest,
                                 f'{name}: reviewed evidence changed: {relative}')
        for trial in schedule:
            row = self.check_trial(trial, manifest, reviews)
            if row is not None:
                self.rows.append(row)
        self.require(len(self.thread_ids) == len(set(self.thread_ids)), 'agent thread IDs were reused across fresh trials')
        trial_root = self.campaign / 'trials'
        extras = [path.name for path in trial_root.iterdir() if path.is_dir() and path.name not in ids] if trial_root.exists() else []
        self.require(not extras, f'unscheduled trial directories: {extras}')
        completion = self.read(Path('execution-complete.json'), pending=True)
        if completion:
            self.require(completion.get('scheduled') == len(schedule), 'completion scheduled count differs')
            self.require(len(self.rows) == len(schedule), 'completion exists with missing dispositions')
        summary = self.read(Path('summary.json'), pending=True)
        if summary:
            self.require(summary.get('scheduled_trials') == len(schedule), 'summary scheduled count differs')
            summarized = {row['id']: row for row in summary.get('rows', [])}
            self.require(len(summarized) == len(schedule) and len(summary.get('rows', [])) == len(schedule), 'summary is missing or duplicating rows')
            for row in self.rows:
                for key, value in row.items():
                    self.require(summarized.get(row['id'], {}).get(key) == value, f"summary {row['id']}: {key} differs")
            if len(self.rows) == len(schedule):
                for language in LANGUAGES:
                    for task in TASKS:
                        rows = [row for row in self.rows if row['language'] == language and row['task'] == task]
                        cell = summary.get('by_language_and_task', {}).get(language, {}).get(task, {})
                        for key, value in {'scheduled': len(rows), 'final_pass': sum(row['final_pass'] for row in rows),
                                           'mechanical_pass': sum(row['mechanical_pass'] is True for row in rows),
                                           'reviews_complete': all(row['reviews_complete'] for row in rows)}.items():
                            self.require(cell.get(key) == value, f'summary {language}/{task}: {key} differs')
                        for metric in ('wall_seconds', 'observed_tool_items', 'input_tokens', 'cached_input_tokens', 'output_tokens'):
                            values = [row[metric] for row in rows if row[metric] is not None]
                            expected_metric = {'observed': len(values), 'median': statistics.median(values) if values else None,
                                               'min': min(values) if values else None, 'max': max(values) if values else None}
                            self.require(cell.get('metrics_all_dispositions', {}).get(metric) == expected_metric,
                                         f'summary {language}/{task}: {metric} statistics differ')
            csv_path = self.campaign / 'trials.csv'
            if csv_path.is_file():
                with csv_path.open(newline='') as stream:
                    csv_rows = list(csv.DictReader(stream))
                self.require(len(csv_rows) == len(summary.get('rows', [])), 'CSV row count differs')
                for source, emitted in zip(summary.get('rows', []), csv_rows):
                    for key, value in source.items():
                        self.require(emitted.get(key) == ('' if value is None else str(value)), f"CSV {source['id']}: {key} differs")
            else:
                self.pending.append('missing trials.csv')
        return self.report()

    def report(self):
        return {'schema': 'quotation-v1-independent-evidence-check',
                'campaign': str(self.campaign),
                'status': 'inconsistent' if self.errors else ('incomplete' if self.pending else 'consistent'),
                'errors': self.errors, 'pending': self.pending, 'warnings': self.warnings,
                'recorded_dispositions': len(self.rows),
                'mechanical_passes': sum(row['mechanical_pass'] is True for row in self.rows),
                'reviewed_final_passes': sum(row['final_pass'] for row in self.rows),
                'limitations': [
                    'This checker validates evidence consistency, not subject semantics or model-review truth.',
                    'Wall time lacks raw timestamp anchors; only finite/nonnegative values and deadline consistency can be checked.',
                    'Oracle output hashes cannot be regenerated without rerunning subjects; the retained process report and counts are reconciled.',
                    'Evaluator visibility is cooperative; installed toolchain contents are not fully frozen.',
                    'Running campaigns can change while inspected; final validation requires a completed quiescent campaign.',
                ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign', type=Path)
    parser.add_argument('--allow-incomplete', action='store_true')
    args = parser.parse_args()
    audit = Audit(args.campaign)
    try:
        report = audit.run()
    except (KeyError, TypeError, ValueError, OSError) as error:
        audit.errors.append(f'cannot reconcile malformed evidence: {type(error).__name__}: {error}')
        report = audit.report()
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0 if report['status'] == 'consistent' or (args.allow_incomplete and report['status'] == 'incomplete') else 1


if __name__ == '__main__':
    raise SystemExit(main())
