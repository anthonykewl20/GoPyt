"""Measure the compiler-derived context footprint of a change (RP-001 evidence).

For a package and a set of changed functions, compute the affected closure that
`gopyt.obligations` would hand to an agent (reverse call closure plus readers of
record fields the changed functions write), and compare its size with the whole
repository. The output is a measurement of how much context the tool selects;
it does not prove the selection is sufficient. A footprint that omits a function
the change actually depends on is a soundness failure, not a bounded-context win,
so the report also lists what is excluded and why.

Usage:
    python tools/context_footprint.py examples/orders --changed orders.refunds.refund
    python tools/context_footprint.py examples/orders --every-function --output out.json
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gopyt.obligations import _base_symbol, _reverse_reach, compute_facts, load_registry  # noqa: E402
from gopyt.project_context import create_receipt  # noqa: E402


def body_lines(root: Path, facts: dict) -> dict[str, int]:
    """Approximate lines per function from its file and the ordering of declarations."""
    per_file: dict[str, list[tuple[int, str]]] = {}
    for key, row in facts['functions'].items():
        line = row.get('line')
        per_file.setdefault(row['file'], []).append((line if line is not None else 0, key))
    sizes = {}
    for file, rows in per_file.items():
        text = (root / file).read_text().splitlines()
        rows.sort()
        # Without per-function line numbers, split the file evenly among its functions.
        share = len(text) / max(len(rows), 1)
        for _line, key in rows:
            sizes[key] = share
    return sizes


def footprint(root: Path, facts: dict, declarations: list[dict], changed: list[str], registry: dict | None) -> dict:
    functions = facts['functions']
    changed_keys = {k for k in functions if _base_symbol(k) in set(changed)}
    changed_fields = set()
    for k in changed_keys:
        for w in functions[k]['writes']:
            changed_fields.add((w['type'], w['field']))
    coupling = {(c['type'], c['field']): c for c in facts['field_coupling']}
    field_readers = set()
    for tf in changed_fields:
        if tf in coupling:
            field_readers |= set(coupling[tf]['readers'])
    affected = _reverse_reach(facts['edges'], changed_keys | field_readers)
    affected_non_test = {k for k in affected if functions.get(k, {}).get('kind') != 'test'}
    modules = {functions[k]['module'] for k in affected if k in functions}
    decls = [d for d in declarations if d['module'] in modules]
    all_non_test = {k for k, v in functions.items() if v['kind'] != 'test'}
    lines = {}
    for file in {v['file'] for v in functions.values()} | {d['file'] for d in declarations}:
        try:
            lines[file] = len((root / file).read_text().splitlines())
        except OSError:
            lines[file] = 0
    affected_files = {functions[k]['file'] for k in affected_non_test if k in functions} | {d['file'] for d in decls}
    all_files = set(lines)
    applying = []
    if registry:
        base_affected = {_base_symbol(k) for k in affected}
        for row in registry['obligations']:
            if set(row.get('functions', [])) & base_affected or \
                    {f for _t, f in changed_fields} & set(row.get('state_fields', [])):
                applying.append(row['id'])
    return {
        'changed': sorted(changed),
        'affected_functions': sorted(affected_non_test),
        'affected_via_field_coupling': sorted(field_readers - changed_keys),
        'affected_modules': sorted(modules),
        'declarations_selected': [d['id'] for d in decls],
        'obligations_applying': applying,
        'counts': {
            'functions_total': len(all_non_test), 'functions_selected': len(affected_non_test),
            'modules_total': len({v['module'] for v in functions.values() if v['kind'] != 'test'}),
            'modules_selected': len(modules),
            'declarations_total': len(declarations), 'declarations_selected': len(decls),
            'files_total': len(all_files), 'files_selected': len(affected_files),
            'lines_total': sum(lines.values()),
            'lines_selected': sum(lines[f] for f in affected_files),
        },
        'excluded_reason': 'Functions outside the reverse call closure and not reading a field the change '
                           'writes. Excluded functions can still matter through effects, external state, '
                           'or prose rules with no representation; the tool reports those as unknowns.',
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('root', type=Path)
    parser.add_argument('--changed', action='append', default=[], help='function symbol, repeatable')
    parser.add_argument('--every-function', action='store_true',
                        help='measure one single-function change per non-test function')
    parser.add_argument('--output', type=Path, default=None)
    args = parser.parse_args(argv)
    root = Path(os.path.abspath(args.root))
    facts = compute_facts(root)
    receipt = create_receipt(root, 'context')
    declarations = receipt['context']['public_declarations'] if receipt['context'] else []
    registry = None
    if (root / 'obligations.json').is_file():
        registry = load_registry(root)
    if args.every_function:
        rows = [footprint(root, facts, declarations, [_base_symbol(k)], registry)
                for k, v in sorted(facts['functions'].items()) if v['kind'] != 'test' and v['body_sha256']]
    else:
        rows = [footprint(root, facts, declarations, args.changed, registry)]
    result = {'schema': 'gopyt.context-footprint.v1', 'root': str(root),
              'source_sha256': receipt['source']['sha256'], 'measurements': rows,
              'meaning': 'Selected context size relative to repository size for each change. '
                         'Smaller is not automatically better: a selection is only useful if it is '
                         'sound for the change, which this measurement does not establish.'}
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        with args.output.open('x') as stream:
            stream.write(text + '\n')
    counts = [r['counts'] for r in rows]
    print(f"{'changed':48} {'fn sel/tot':>11} {'decl sel/tot':>13} {'lines sel/tot':>14} {'oblig':>6}")
    for r, c in zip(rows, counts):
        print(f"{','.join(r['changed'])[:48]:48} {c['functions_selected']:>4}/{c['functions_total']:<6} "
              f"{c['declarations_selected']:>5}/{c['declarations_total']:<7} "
              f"{c['lines_selected']:>6}/{c['lines_total']:<7} {len(r['obligations_applying']):>6}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
