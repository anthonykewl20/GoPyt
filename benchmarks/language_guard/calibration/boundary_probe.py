"""Bounded development differential probing; never an approval or proof tool."""
import argparse
from dataclasses import fields
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def integers(node):
    from gopyt.ast_nodes import Node, IntLit
    if isinstance(node, IntLit):
        yield int(node.value.replace('_', ''))
    elif isinstance(node, Node):
        for field in fields(node):
            yield from integers(getattr(node, field.name))
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from integers(item)


def vectors(cases, constants, limit):
    """Deterministic one-coordinate changes; preserve bool versus int identity."""
    values = [-1, 0, 1, -(2**63), 2**63 - 1]
    for number in sorted(set(constants)):
        for sign in (number, -number):
            values.extend([sign - 1, sign, sign + 1])
    values = list(dict.fromkeys(n for n in values if -(2**63) <= n < 2**63))
    seen = set()
    for case in cases:
        if 'expected' not in case:
            continue
        for index, original in enumerate(case['args']):
            for value in ([not original] if type(original) is bool else values):
                args = list(case['args']); args[index] = value
                key = json.dumps([case['symbol'], args], separators=(',', ':'))
                if key in seen or args == case['args']:
                    continue
                seen.add(key)
                if len(seen) > limit:
                    return
                yield case['symbol'], args


def nodes(node):
    from gopyt.ast_nodes import Node
    if isinstance(node, Node):
        yield node
        for field in fields(node):
            yield from nodes(getattr(node, field.name))
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from nodes(item)


def constant(node):
    from gopyt.ast_nodes import IntLit, BoolLit, UnaryExpr, BinExpr
    if isinstance(node, IntLit):
        value = int(node.value)
    elif isinstance(node, BoolLit):
        return node.value
    elif isinstance(node, UnaryExpr) and node.op == '-':
        inner = constant(node.inner)
        if type(inner) is not int:
            return None
        value = -inner
    elif isinstance(node, BinExpr) and node.op in ('+', '-', '*'):
        left, right = constant(node.left), constant(node.right)
        if type(left) is not int or type(right) is not int:
            return None
        value = {'+': lambda: left + right, '-': lambda: left - right,
                 '*': lambda: left * right}[node.op]()
    else:
        return None
    return value if -(2**63) <= value < 2**63 else None


def affine(node, name):
    """Return a*x+b for one parameter; unsupported expressions remain unknown."""
    from gopyt.ast_nodes import NameExpr, UnaryExpr, BinExpr
    value = constant(node)
    if type(value) is int:
        return 0, value
    if isinstance(node, NameExpr) and node.parts == [name]:
        return 1, 0
    if isinstance(node, UnaryExpr) and node.op == '-':
        inner = affine(node.inner, name)
        return (-inner[0], -inner[1]) if inner else None
    if isinstance(node, BinExpr) and node.op in ('+', '-', '*'):
        left, right = affine(node.left, name), affine(node.right, name)
        if left is None or right is None:
            return None
        if node.op == '+':
            result = left[0]+right[0], left[1]+right[1]
        elif node.op == '-':
            result = left[0]-right[0], left[1]-right[1]
        elif left[0] == 0:
            result = left[1]*right[0], left[1]*right[1]
        elif right[0] == 0:
            result = right[1]*left[0], right[1]*left[1]
        else:
            return None
        if all(abs(value) <= 2**63 for value in result):
            return result
    return None


def directed_vectors(program, cases, limit, stats=None):
    from itertools import product, islice
    from gopyt.ast_nodes import BinExpr
    from gopyt.types import BOOL
    def stream(symbol, choices):
        for args in islice(product(*choices), limit+1):
            yield symbol, list(args)
    streams = []
    for case in cases:
        fn = program.funcs[case['symbol']]
        choices = []
        comparisons = [n for n in nodes(fn.body) if isinstance(n, BinExpr)
                       and n.op in ('==', '!=', '<', '<=', '>', '>=')]
        for (name, ty), seed in zip(fn.params, case['args']):
            if ty == BOOL:
                choices.append([not seed, seed])
                continue
            targets = []
            for cmp in comparisons:
                left, right = affine(cmp.left, name), affine(cmp.right, name)
                if left is None or right is None or left[0] == right[0]:
                    continue
                center = (right[1]-left[1]) // (left[0]-right[0])
                targets.extend(center+delta for delta in (0, -1, 1, -2, 2)
                               if -(2**63) <= center+delta < 2**63)
            unique_targets = list(dict.fromkeys(targets))
            if stats is not None and len(unique_targets) > 16:
                stats['directed_choices_truncated'] = True
            choices.append(unique_targets[:16] or [seed])
        streams.append(stream(case['symbol'], choices))
    while streams:
        active = []
        for iterator in streams:
            try:
                yield next(iterator)
                active.append(iterator)
            except StopIteration:
                pass
        streams = active


def finite_vectors(cases, domains, limit):
    from itertools import product
    import math
    signatures = {case['symbol']: case['args'] for case in cases}
    if type(domains) is not dict or domains.keys() != signatures.keys():
        raise ValueError('domains must cover exactly the acceptance symbols')
    result = []; total = 0
    for symbol in sorted(domains):
        axes = domains[symbol]; args = signatures[symbol]
        if type(axes) is dict and set(axes) == {'tuples'}:
            rows = axes['tuples']
            if type(rows) is not list or not rows:
                raise ValueError('tuple domain must be a nonempty list')
            seen = set()
            for row in rows:
                if type(row) is not list or len(row) != len(args):
                    raise ValueError('tuple domain arity mismatch')
                if any(type(value) is not type(original) or
                       (type(value) is int and not -(2**63) <= value < 2**63)
                       for value, original in zip(row, args)):
                    raise ValueError('tuple domain scalar type or range mismatch')
                key = tuple(row)
                if key not in seen:
                    seen.add(key); total += 1
                    if total > limit:
                        raise ValueError('complete finite domain exceeds probe budget')
                    result.append((symbol, list(row)))
            continue
        if type(axes) is not list or len(axes) != len(args):
            raise ValueError('domain arity mismatch')
        clean = []
        for axis, original in zip(axes, args):
            if type(axis) is not list or not axis:
                raise ValueError('domain axes must be nonempty lists')
            if any(type(value) is not type(original) or
                   (type(value) is int and not -(2**63) <= value < 2**63) for value in axis):
                raise ValueError('domain scalar type or range mismatch')
            clean.append(list(dict.fromkeys(axis)))
        count = math.prod(map(len, clean)); total += count
        if total > limit:
            raise ValueError('complete finite domain exceeds probe budget')
        result.extend((symbol, list(args)) for args in product(*clean))
    return result


def probe(engine, baseline, candidate, raw, pin, limit=2000, domains=None):
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError('limit must be 1..10000')
    sys.path.insert(0, str(engine))
    from gopyt.guard import (inspect_candidate, digest, materialize, _compile_policy,
                             _contract_dependencies, validate_case_signatures, timed_call)
    from gopyt.vm import Trap
    bundle, base_files, changed = inspect_candidate(raw, pin, baseline)
    if changed:
        raise ValueError('baseline must match every approved file hash')
    _, candidate_files, _ = inspect_candidate(raw, pin, candidate)
    domain_selection = finite_vectors(bundle['cases'], domains, limit) if domains is not None else None
    def call(vm, ids, symbol, args):
        try:
            value = timed_call(vm, ids[symbol], args, timeout=0.05)
            return {'expected': value}
        except Trap as exc:
            return {'trap': exc.code}
    with tempfile.TemporaryDirectory(prefix='gopyt-boundary-') as temp:
        roots = [Path(temp)/'baseline', Path(temp)/'candidate']
        compiled = []
        for root, files in zip(roots, [base_files, candidate_files]):
            root.mkdir(); materialize(files, root)
            program, vm, ids = _compile_policy(root)
            validate_case_signatures(program, bundle['cases'])
            if _contract_dependencies(program) != bundle['contract_dependencies']:
                raise ValueError('contract helper meaning changed')
            compiled.append((program, vm, ids))
        base, cand = compiled
        candidate_passed = 0
        for case in bundle['cases']:
            expected = {k: case[k] for k in ('expected', 'trap') if k in case}
            actual = call(base[1], base[2], case['symbol'], case['args'])
            if json.dumps(actual, sort_keys=True) != json.dumps(expected, sort_keys=True):
                raise ValueError('baseline fails approved acceptance case')
            actual = call(cand[1], cand[2], case['symbol'], case['args'])
            candidate_passed += json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)
        constants = (sorted({value for fn in cand[0].funcs.values() for node in nodes(fn.body)
                             if type(value := constant(node)) is int}) if domains is None else [])
        seeds = [case for case in bundle['cases'] if 'expected' in case]
        search_stats = {'directed_choices_truncated': False}
        if domains is not None:
            selected = domain_selection
        else:
            # Represent each symbol before spending remaining seed slots.
            representatives = {case['symbol']: case for case in reversed(seeds)}
            selected_seeds = list(representatives.values())[:32]
            for case in seeds:
                if len(selected_seeds) >= 32:
                    break
                if case not in selected_seeds:
                    selected_seeds.append(case)
            streams = [iter(directed_vectors(cand[0], selected_seeds, limit, search_stats)),
                       iter(vectors(selected_seeds, constants[:128], limit+1))]
            selected = []; seen = set()
            while streams and len(selected) <= limit:
                active = []
                for iterator in streams:
                    try:
                        symbol, args = next(iterator)
                        key = json.dumps([symbol, args], separators=(',', ':'))
                        if key not in seen:
                            seen.add(key); selected.append((symbol, args))
                        active.append(iterator)
                    except StopIteration:
                        pass
                    if len(selected) > limit:
                        break
                streams = active
        differences = []; baseline_traps = 0; mismatches = 0
        for symbol, args in selected[:limit]:
            expected = call(base[1], base[2], symbol, args)
            if 'trap' in expected:
                baseline_traps += 1
            actual = call(cand[1], cand[2], symbol, args)
            if json.dumps(actual, sort_keys=True) != json.dumps(expected, sort_keys=True):
                mismatches += 1
                if len(differences) < 20:
                    differences.append({'symbol': symbol, 'args': args, 'baseline': expected, 'candidate': actual})
        return {'schema': 'gopyt.development.boundary-probe.v2', 'completed': True,
                'bundle_sha256': digest(raw), 'baseline_files': {p: digest(b) for p,b in base_files.items()},
                'candidate_files': {p: digest(b) for p,b in candidate_files.items()},
                'candidate_acceptance_passed': candidate_passed, 'acceptance_total': len(bundle['cases']),
                'generated_executed': min(limit, len(selected)), 'baseline_traps_skipped': 0, 'baseline_traps_compared': baseline_traps,
                'mismatches': mismatches, 'counterexamples': differences,
                'mode': 'finite-domain' if domains is not None else 'heuristic',
                'domain_fully_compared': domains is not None, 'domains': domains,
                'domain_equivalent': (mismatches == 0) if domains is not None else None,
                'heuristic_is_exhaustive': False,
                'generated_uncovered_symbols': sorted({c['symbol'] for c in bundle['cases']} - {s for s, _ in selected[:limit]}),
                'probe_sha256': digest(Path(__file__).read_bytes()),
                'search_limits': {'seeds': 32, 'constants': 128, 'directed_values_per_parameter': 16},
                'truncated': {'seeds': domains is None and len(seeds) > 32, 'constants': domains is None and len(constants) > 128,
                              'probes': len(selected) > limit, 'counterexamples': mismatches > 20,
                              'directed_choices': search_stats['directed_choices_truncated']},
                'limits': 'Development baseline comparison, not approval or proof. Bounded directed combinations and scalar boundaries; finite-domain completeness applies only to declared tuples. Public or previously used cases are not a sealed holdout.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['engine', 'baseline', 'candidate', 'bundle', 'out']:
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--pin', required=True)
    parser.add_argument('--limit', type=int, default=2000)
    parser.add_argument('--domains', type=Path, help='JSON mapping each symbol to finite argument axes')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.limit <= 10000:
        parser.error('limit must be 1..10000')
    if not args.worker:
        # Parent publishes only a completed JSON result, never partial success.
        result = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()),
                                 *sys.argv[1:], '--worker'], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stderr[-2000:])
        report = json.loads(result.stdout)
        with args.out.open('x') as stream:
            json.dump(report, stream, indent=2); stream.write('\n')
        print(json.dumps({'mismatches': report['mismatches'], 'out': str(args.out)}))
        return
    if sys.platform.startswith('linux'):
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    with args.bundle.open('rb') as stream:
        raw = stream.read(2 * 1024**2 + 1)
    if len(raw) > 2 * 1024**2:
        raise ValueError('bundle exceeds byte limit')
    domains = None
    if args.domains is not None:
        sys.path.insert(0, str(args.engine.resolve()))
        from gopyt.guard import unique_json
        with args.domains.open('rb') as stream:
            data = stream.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError('domain file exceeds byte limit')
        domains = unique_json(data)
        if domains is None:
            raise ValueError('domain file must contain an object')
    print(json.dumps(probe(args.engine.resolve(), args.baseline.resolve(), args.candidate.resolve(),
                           raw, args.pin, args.limit, domains)))


if __name__ == '__main__':
    main()
