#!/usr/bin/env python3
"""Verify the frozen workload, threat model and qualification targets.

This recomputes the freeze digest, re-verifies every referenced dataset identity
against the retained preparation manifest, and checks that the frozen JSON and
its document agree on every frozen scalar. It is an integrity check on the
freeze itself, not a measurement and not a security review.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

FREEZE = Path('validation/workload-freeze/targets.json')


def _fail(problems, message):
    problems.append(message)


def _digest(document):
    body = {key: value for key, value in document.items() if key != 'digest'}
    canonical = json.dumps(body, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return 'sha256:' + hashlib.sha256(canonical).hexdigest()


def _numbers(text):
    """Every integer and decimal in the document, without digit-group commas."""
    plain = text.replace('’', "'")
    return {match.group(0).replace(',', '')
            for match in re.finditer(r'\d[\d,]*(?:\.\d+)?', plain)}


def check(root):
    problems = []
    freeze_path = root / FREEZE
    document = json.loads(freeze_path.read_text())
    if document.get('schema') != 'gopyt.workload-freeze.v1':
        _fail(problems, f'unexpected schema {document.get("schema")!r}')
    if not isinstance(document.get('revision'), int) or document['revision'] < 1:
        _fail(problems, 'revision must be a positive integer')
    if document.get('frozen_before_tuning') is not True:
        _fail(problems, 'frozen_before_tuning must remain true')
    expected = _digest(document)
    if document.get('digest') != expected:
        _fail(problems, f'digest mismatch: recorded {document.get("digest")}, '
                        f'recomputed {expected}')

    frozen = document.get('frozen', {})
    primary = frozen.get('datasets', {}).get('primary', {})
    manifest_name = primary.get('manifest')
    if not manifest_name:
        _fail(problems, 'the primary dataset must name its retained manifest')
    else:
        manifest_path = root / manifest_name
        if not manifest_path.exists():
            _fail(problems, f'missing retained manifest {manifest_name}')
        else:
            manifest = json.loads(manifest_path.read_text())
            if manifest.get('xlsx_sha256') != primary.get('xlsx_sha256'):
                _fail(problems, 'frozen source workbook hash differs from the manifest')
            for name, value in sorted(primary.get('artifacts', {}).items()):
                if manifest.get('sha256', {}).get(name) != value:
                    _fail(problems, f'frozen artifact hash for {name} differs from the manifest')
            for name, value in sorted(primary.get('counts', {}).items()):
                if manifest.get('counts', {}).get(name) != value:
                    _fail(problems, f'frozen dataset count {name} differs from the manifest')

    document_name = document.get('document')
    if not document_name:
        _fail(problems, 'the freeze must name its document')
    else:
        document_path = root / document_name
        if not document_path.exists():
            _fail(problems, f'missing frozen document {document_name}')
        else:
            text = document_path.read_text()
            present = _numbers(text)
            budgets = frozen.get('budgets', {})
            for name, value in sorted(budgets.items()):
                if str(value) not in present and f'{value:,}'.replace(',', '') not in present:
                    _fail(problems, f'budget {name}={value} does not appear in {document_name}')
            for name, value in sorted(primary.get('counts', {}).items()):
                if str(value) not in present:
                    _fail(problems, f'dataset count {name}={value} does not appear in {document_name}')
            for name, value in sorted(primary.get('artifacts', {}).items()):
                if value not in text:
                    _fail(problems, f'artifact hash for {name} does not appear in {document_name}')
            for gap in frozen.get('known_gaps', []):
                marker = '#' + str(gap.get('issue'))
                if marker not in text:
                    _fail(problems, f'known gap {marker} is not named in {document_name}')

    for name in ('use_cases', 'production_platform', 'hardware'):
        if name not in frozen.get('envelope', {}):
            _fail(problems, f'the envelope must freeze {name}')
    for name in ('invariants', 'budgets', 'control_allocation', 'threat_model',
                 'datasets', 'unsupported', 'known_gaps'):
        if not frozen.get(name):
            _fail(problems, f'the freeze must record {name}')
    for owner in ('language', 'runtime', 'application', 'deployment'):
        if not frozen.get('control_allocation', {}).get(owner):
            _fail(problems, f'control allocation must name {owner} controls')
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)
    problems = check(args.root)
    if problems:
        for problem in problems:
            print('workload freeze: ' + problem, file=sys.stderr)
        return 1
    print('ok frozen workload targets')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
