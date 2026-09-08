"""Explicit business obligations, verification scope and change context.

Developer tooling layered on `gopyt.project_context`. It changes no language
syntax, bytecode or CLI command. A registry (`obligations.json` at the package
root) gives each business requirement a stable id and says how, if at all, it is
represented in the checked program. This module binds those representations to
compiler-derived facts and to retained execution evidence, and reports what was
established, against which inputs, and what remains unverified.

Nothing here is a proof. A bound `ensures` clause is enforced on every call the
VM actually executes; a passing test is evidence for the inputs it used; an
acceptance report is evidence for the corpus it ran. All three become stale when
the sources, dependencies, engine or contract text they were bound to change.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from gopyt import ast_nodes as ast
from gopyt.check import CallRes, ParallelRes, check_package, load_package
from gopyt.diag import CompileError, format_diag
from gopyt.fmt import fmt_expr, fmt_item, fmt_test
from gopyt.project_context import (
    canonical, capture_graph, create_receipt, digest, engine_identity, materialize,
    validate_receipt, without_comments,
)

REGISTRY_SCHEMA = 'gopyt.obligations.v1'
REPORT_SCHEMA = 'gopyt.obligation-report.v1'
IMPACT_SCHEMA = 'gopyt.change-impact.v1'
FACTS_SCHEMA = 'gopyt.semantic-facts.v1'

AUTHORITIES = ('contract', 'proposed', 'unresolved')
EXECUTABLE_KINDS = ('requires', 'ensures', 'preserved_field', 'test', 'acceptance')
REPRESENTATION_KINDS = EXECUTABLE_KINDS + ('prose',)
TRAP_MEANING = {1: 'requires violated', 2: 'ensures violated', 3: 'integer overflow', 4: 'division by zero',
                5: 'index out of range', 13: 'assert_eq failed'}


class RegistryError(ValueError):
    pass


# ---------------------------------------------------------------- semantic facts

def _field_ref(expr, scope: dict[str, str]):
    """(base name, base type, field) when `expr` is `name.field` on a typed local/parameter."""
    if isinstance(expr, ast.NameExpr) and len(expr.parts) == 2 and expr.parts[0] in scope:
        return expr.parts[0], scope[expr.parts[0]], expr.parts[1]
    if isinstance(expr, ast.FieldExpr) and isinstance(expr.base, ast.NameExpr) \
            and len(expr.base.parts) == 1 and expr.base.parts[0] in scope:
        return expr.base.parts[0], scope[expr.base.parts[0]], expr.name
    return None


def _classify_source(expr, params: dict[str, str], scope: dict[str, str]) -> str:
    """Describe where a constructed field value comes from, conservatively."""
    if isinstance(expr, (ast.IntLit, ast.StrLit, ast.BoolLit, ast.NoneLit, ast.UnitLit)):
        return 'literal:' + fmt_expr(expr)
    ref = _field_ref(expr, scope)
    if ref is not None:
        base, _ty, field = ref
        return ('param_field:' if base in params else 'local_field:') + base + '.' + field
    if isinstance(expr, ast.NameExpr):
        if len(expr.parts) >= 2:
            return 'enum:' + '.'.join(expr.parts)
        if expr.parts[0] in params:
            return 'param:' + expr.parts[0]
        return 'local:' + expr.parts[0]
    return 'computed'


def _walk(node):
    if isinstance(node, ast.Node):
        yield node
        for value in vars(node).values():
            yield from _walk(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _walk(item)


def semantic_facts(pkg, prog) -> dict:
    """Compiler-derived facts beyond imports: bodies, field writes/reads, comparisons."""
    decls = {}
    for group in (pkg.impl, pkg.dep_impls):
        for mod in group.values():
            for item in mod.items:
                if isinstance(item, ast.FnDecl):
                    decls[mod.path + '.' + item.sig.name] = (mod, item)
    for mod in pkg.tests.values():
        for test in mod.tests:
            decls[mod.path + '.' + test.name] = (mod, test)
    functions = {}
    for fc in prog.funcs.values():
        if fc.parent or fc.body is None:
            continue
        params = {name: ty.key for name, ty in fc.params}
        body_digest = None
        if fc.symbol in decls:
            mod, item = decls[fc.symbol]
            clean = without_comments(item)
            text = fmt_test(clean) if isinstance(clean, ast.TestDecl) else fmt_item(clean)
            body_digest = digest(text.encode())
        # Scope: parameters plus every bound local whose checked type is known.
        scope = dict(params)
        for node in _walk(fc.body):
            if isinstance(node, (ast.BindStmt, ast.ForStmt)):
                ty = fc.ty.get(id(node.expr if isinstance(node, ast.BindStmt) else node.iter))
                if ty is not None and node.name not in scope:
                    scope[node.name] = ty.key
        writes, reads, compares = [], [], []
        for node in _walk(fc.body):
            if isinstance(node, ast.ConstructExpr) and not node.enum:
                ty = fc.ty.get(id(node))
                type_key = ty.key if ty is not None else node.ty
                for name, value in node.fields:
                    writes.append({'type': type_key, 'field': name,
                                   'source': _classify_source(value, params, scope), 'line': node.line})
            ref = _field_ref(node, scope)
            if ref is not None:
                reads.append({'type': ref[1], 'field': ref[2]})
            if isinstance(node, ast.BinExpr) and node.op in ('==', '!='):
                sides = (node.left, node.right)
                for a, b in (sides, sides[::-1]):
                    ref = _field_ref(a, scope)
                    if ref is not None and isinstance(b, ast.NameExpr) and len(b.parts) >= 2 \
                            and b.parts[0] not in scope:
                        compares.append({'type': ref[1], 'field': ref[2], 'op': node.op,
                                         'variant': '.'.join(b.parts), 'line': node.line})
        unique_reads = sorted({(r['type'], r['field']) for r in reads})
        functions[fc.key] = {
            'symbol': fc.symbol, 'kind': fc.kind, 'file': fc.file, 'module': fc.module,
            'parameters': [{'name': n, 'type': t} for n, t in params.items()],
            'returns': fc.ret.key, 'effects': sorted(fc.effects), 'body_sha256': body_digest,
            'writes': writes, 'reads': [{'type': t, 'field': f} for t, f in unique_reads],
            'compares': compares,
        }
    edges = set()
    for fc in prog.funcs.values():
        for resolution in fc.res.values():
            if isinstance(resolution, CallRes):
                edges.add((fc.key, resolution.key, 'call'))
            elif isinstance(resolution, ParallelRes):
                for arm in resolution.arms:
                    edges.add((fc.key, arm.symbol, 'parallel_arm'))
    for module, verb, path, handler in prog.routes:
        edges.add((module + '.serve', handler, 'http_dispatch'))
    # State coupling: a writer of a record field is a semantic dependency of every
    # reader of that field, whether or not the reader imports the writer.
    writers, readers = {}, {}
    for key, row in functions.items():
        for w in row['writes']:
            writers.setdefault((w['type'], w['field']), set()).add(key)
        for r in row['reads']:
            readers.setdefault((r['type'], r['field']), set()).add(key)
    coupling = []
    for field_key in sorted(set(writers) | set(readers)):
        coupling.append({'type': field_key[0], 'field': field_key[1],
                         'writers': sorted(writers.get(field_key, ())),
                         'readers': sorted(readers.get(field_key, ()))})
    contracts = []
    for group in (pkg.spec, pkg.dep_specs):
        for mod in group.values():
            for item in mod.items:
                if isinstance(item, ast.FnDecl):
                    for c in item.sig.contracts:
                        contracts.append({'function': mod.path + '.' + item.sig.name,
                                          'kind': c.kind, 'open': c.open_id,
                                          'text': None if c.open_id else _clause_text(c),
                                          'file': mod.file, 'line': c.line})
    return {'schema': FACTS_SCHEMA,
            'functions': dict(sorted(functions.items())),
            'edges': [{'caller': a, 'callee': b, 'kind': k} for a, b, k in sorted(edges)],
            'field_coupling': coupling,
            'contracts': contracts,
            'scope': 'Bodies of compiled, instantiated functions and tests. Field sources are '
                     'syntactic (literal, enum, parameter field, computed); anything else is '
                     '"computed" and must be established at runtime. Effects are declared, '
                     'not traced; external behavior is not modeled.'}


def _clause_text(c) -> str:
    return _normalize(c.kind + ' ' + fmt_expr(c.expr, 4))


def _normalize(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


# ---------------------------------------------------------------- registry

def load_registry(root: Path, strict: bool = True) -> dict:
    path = root / 'obligations.json'
    if not path.is_file():
        raise RegistryError('no obligations.json at package root')
    raw = path.read_bytes()
    registry = json.loads(raw)
    if not isinstance(registry, dict) or registry.get('schema') != REGISTRY_SCHEMA:
        raise RegistryError('unsupported registry schema')
    if not isinstance(registry.get('obligations'), list):
        raise RegistryError('registry must contain an obligations list')
    seen = set()
    row_errors = {}
    kept = []
    for index, row in enumerate(registry['obligations']):
        problems = []
        for key in ('id', 'title', 'authority', 'representations'):
            if key not in row:
                problems.append(f'missing {key}')
        ident = row.get('id') if isinstance(row.get('id'), str) else f'<row {index}>'
        if isinstance(row.get('id'), str) and not re.fullmatch(r'[A-Z][A-Z0-9]*(-[A-Z0-9]+)+', row['id']):
            problems.append(f'unstable obligation id: {row["id"]}')
        if ident in seen:
            problems.append(f'duplicate obligation id: {ident}')
        seen.add(ident)
        if row.get('authority') not in AUTHORITIES:
            problems.append(f'authority must be one of {AUTHORITIES}')
        for rep in row.get('representations', []) if isinstance(row.get('representations'), list) else []:
            if not isinstance(rep, dict) or rep.get('kind') not in REPRESENTATION_KINDS:
                problems.append(f'unknown representation kind {rep.get("kind") if isinstance(rep, dict) else rep}')
            elif row.get('authority') == 'unresolved' and rep['kind'] in EXECUTABLE_KINDS:
                problems.append(f'an unresolved policy cannot carry an executable representation '
                                f'({rep["kind"]}); decide the policy first')
        if problems:
            row_errors[ident] = problems
        else:
            kept.append(row)
    registry['_row_errors'] = row_errors
    registry['obligations'] = kept
    if strict and row_errors:
        raise RegistryError('; '.join(f'{k}: {v}' for k, v in row_errors.items()))
    registry['_sha256'] = digest(raw)
    return registry


def _contract_binding(root: Path, registry: dict) -> dict:
    """Bind the registry to the prose document it was written from."""
    info = registry.get('contract')
    if not info:
        return {'status': 'unbound', 'meaning': 'registry cites no authoritative prose document'}
    path = (root / info['path']).resolve()
    if not path.is_file():
        return {'status': 'missing', 'path': info['path']}
    current = digest(path.read_bytes())
    return {'status': 'current' if current == info.get('sha256') else 'stale',
            'path': info['path'], 'recorded_sha256': info.get('sha256'), 'current_sha256': current,
            'meaning': 'stale means the prose contract changed after the registry was written; '
                       'every obligation needs review against the new text'}


# ---------------------------------------------------------------- evidence

def _reach(edges: list[dict], start: str) -> set[str]:
    forward = {}
    for e in edges:
        forward.setdefault(e['caller'], set()).add(e['callee'])
    seen, pending = set(), [start]
    while pending:
        node = pending.pop()
        for nxt in forward.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                pending.append(nxt)
    return seen


def _reverse_reach(edges: list[dict], starts: set[str]) -> set[str]:
    backward = {}
    for e in edges:
        backward.setdefault(e['callee'], set()).add(e['caller'])
    seen, pending = set(starts), list(starts)
    while pending:
        node = pending.pop()
        for prev in backward.get(node, ()):
            if prev not in seen:
                seen.add(prev)
                pending.append(prev)
    return seen


def _base_symbol(key: str) -> str:
    return key.split('[', 1)[0]


def bind_test_receipt(record: dict, current: dict) -> dict:
    """A test receipt is evidence only while its inputs are the current inputs."""
    try:
        validate_receipt(record)
    except ValueError as exc:
        return {'status': 'invalid', 'reason': str(exc), 'cases': {}}
    reasons = []
    if record.get('operation') != 'test':
        reasons.append('not a test receipt')
    if (record.get('source') or {}).get('sha256') != current['source']['sha256']:
        reasons.append('source identity differs from current sources')
    if (record.get('engine') or {}).get('sha256') != current['engine']['sha256']:
        reasons.append('engine identity differs from current compiler')
    cases = {row['name']: row for row in record.get('tests', {}).get('cases', [])}
    planned = record.get('tests', {}).get('planned', [])
    return {'status': 'stale' if reasons else 'current', 'reasons': reasons,
            'receipt_sha256': record.get('receipt_sha256'),
            'test_status': record.get('tests', {}).get('status'),
            'cases': cases, 'planned': planned}


ACCEPTANCE_SCHEMA_VERSIONS = (1, 2)
BINDING_STATUSES = ('passed', 'failed', 'stale', 'partial', 'contradictory', 'unbound', 'invalid')


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def _current_package_files(root: Path, current: dict, repo_root: Path) -> dict[str, str]:
    """Every current file of every package in the source graph.

    Keys are canonical repo-root-relative posix paths, the same namespace an
    acceptance report's `source_before` inventory is resolved in, so coverage
    compares like with like regardless of where the package sits.
    """
    files = {}
    for pkg_rel, entries in current['source']['packages'].items():
        base = Path(root) if pkg_rel == '.' else Path(root) / pkg_rel
        for name, value in entries.items():
            rel = os.path.relpath(Path(os.path.abspath(base / name)), Path(os.path.abspath(repo_root)))
            files[rel.replace(os.sep, '/')] = value
    return files


def bind_acceptance(path: Path, root: Path, current: dict, repo_root: Path) -> dict:
    """Bind an external acceptance report to the complete current inputs.

    A report may only count as `passed` when it carries a versioned evidence
    schema and a suite identity, its `source_before` inventory (paths relative
    to the repository root) covers every current package file and every
    compiler file with matching digests, and its claimed outcome is consistent
    with its own scenario counts and failures. Anything less stays partial,
    stale, contradictory or unbound.
    """
    path = Path(path)
    root = Path(os.path.abspath(root))
    repo_root = Path(os.path.abspath(repo_root))
    try:
        report = json.loads(path.read_text(), object_pairs_hook=_no_duplicate_keys)
    except (OSError, ValueError) as exc:
        return {'status': 'invalid', 'path': str(path), 'reason': f'{type(exc).__name__}: {exc}'}
    if not isinstance(report, dict):
        return {'status': 'invalid', 'path': str(path), 'reason': 'report is not a JSON object'}
    base = {'path': str(path), 'schema_version': report.get('schema_version'),
            'suite': report.get('suite') or report.get('kind'), 'kind': report.get('kind') or report.get('task')}
    if report.get('schema_version') not in ACCEPTANCE_SCHEMA_VERSIONS:
        return base | {'status': 'unbound',
                       'reason': f'no versioned evidence schema: schema_version {report.get("schema_version")!r} '
                                 f'is not one of {ACCEPTANCE_SCHEMA_VERSIONS}'}
    if not isinstance(base['suite'], str) or not base['suite']:
        return base | {'status': 'unbound', 'reason': 'report carries no suite/verifier identity'}
    inventory = report.get('source_before')
    if not isinstance(inventory, dict) or not inventory:
        return base | {'status': 'unbound',
                       'reason': 'report carries no source_before inventory; cannot bind to inputs'}
    engine_files = current['engine']['files']
    engine_dir = Path(__file__).resolve().parent
    current_package = _current_package_files(root, current, repo_root)

    def _engine_slot(target: Path) -> bool:
        """A compiler-file slot: the running engine's own directory or this
        repository's gopyt/ directory. Anything else — including a byte copy
        under a differently-located directory that happens to be named gopyt —
        is an ordinary recorded file, not compiler coverage."""
        return (target.suffix == '.py' and target.name in engine_files
                and (target.parent == engine_dir
                     or target.parent == (repo_root / 'gopyt').resolve()))

    stale, checked, engine_checked, foreign = [], 0, 0, []
    recorded_package, recorded_engine = set(), set()
    for rel, raw in sorted(inventory.items()):
        if not isinstance(rel, str):
            return base | {'status': 'invalid', 'reason': f'inventory key {rel!r} is not a path string'}
        recorded = raw if isinstance(raw, str) and raw.startswith('sha256:') else 'sha256:' + str(raw)
        target = (repo_root / rel).resolve()
        if target != repo_root and repo_root not in target.parents:
            foreign.append(rel)
            continue
        try:
            inside = target.relative_to(root)
        except ValueError:
            inside = None
        if inside is not None:
            checked += 1
            recorded_package.add(os.path.relpath(target, repo_root).replace(os.sep, '/'))
            actual = digest(target.read_bytes()) if target.is_file() and not target.is_symlink() else None
            if actual != recorded:
                stale.append({'file': rel, 'recorded': recorded, 'current': actual})
        elif _engine_slot(target):
            engine_checked += 1
            recorded_engine.add(target.name)
            if engine_files[target.name] != recorded:
                stale.append({'file': rel, 'recorded': recorded, 'current': engine_files[target.name]})
        else:
            actual = digest(target.read_bytes()) if target.is_file() and not target.is_symlink() else None
            if actual != recorded:
                stale.append({'file': rel, 'recorded': recorded, 'current': actual})
    if foreign:
        return base | {'status': 'unbound',
                       'reason': 'inventory names files outside the repository: ' + ', '.join(sorted(foreign))}
    if checked == 0:
        return base | {'status': 'unbound', 'reason': 'inventory names no file inside the package'}
    missing_package = sorted(set(current_package) - recorded_package)
    missing_engine = sorted(set(engine_files) - recorded_engine)
    partial = bool(missing_package or missing_engine)
    failures = report.get('failures')
    failure_count = len(failures) if isinstance(failures, list) else None
    total, passed_count = report.get('scenarios_total'), report.get('scenarios_passed')
    claimed_pass = report.get('passed') is True
    consistent = (isinstance(failures, list) and not failures
                  and type(total) is int and total > 0
                  and type(passed_count) is int and passed_count == total)
    if claimed_pass and not consistent:
        status = 'contradictory'
    elif partial:
        status = 'partial'
    elif stale:
        status = 'stale'
    else:
        status = 'passed' if claimed_pass else 'failed'
    return base | {'status': status, 'passed': claimed_pass and status == 'passed',
                   'stale_files': stale,
                   'missing_package_files': missing_package, 'missing_engine_files': missing_engine,
                   'package_files_checked': checked, 'engine_files_checked': engine_checked,
                   'scenarios_passed': passed_count, 'scenarios_total': total,
                   'failures': failure_count}


# ---------------------------------------------------------------- report

def _preserved_field(rep: dict, facts: dict) -> dict:
    """Static check: every constructor of `type` outside `except` copies `field` from a parameter."""
    type_key, field, exempt = rep['type'], rep['field'], set(rep.get('except', []))
    known = {_base_symbol(k) for k in facts['functions']} | {c['function'] for c in facts['contracts']}
    unknown_exempt = sorted(exempt - known)
    violations, preserved, unknown = [], [], []
    for key, row in facts['functions'].items():
        if row['kind'] == 'test' or row['symbol'] in exempt:
            continue
        for w in row['writes']:
            if w['type'] != type_key or w['field'] != field:
                continue
            source = w['source']
            if source.startswith('param_field:') and source.endswith('.' + field):
                param = source[len('param_field:'):].split('.')[0]
                ptype = next((p['type'] for p in row['parameters'] if p['name'] == param), None)
                if ptype == type_key:
                    preserved.append({'function': key, 'line': w['line']})
                    continue
            if source == 'computed' or source.startswith('local:') or source.startswith('local_field:'):
                unknown.append({'function': key, 'line': w['line'], 'source': source})
            else:
                violations.append({'function': key, 'line': w['line'], 'source': source})
    if violations:
        status = 'violated'
    elif unknown:
        status = 'not_established'
    elif preserved:
        status = 'holds'
    else:
        status = 'no_constructors'
    if status == 'no_constructors':
        return {'status': status, 'preserved': [], 'violations': [], 'not_syntactic': [],
                'meaning': 'No compiled constructor writes this field: check the type and field names in the registry.'}
    return {'status': status, 'preserved': preserved, 'violations': violations, 'not_syntactic': unknown,
            'unknown_except': unknown_exempt,
            'meaning': 'Syntactic preservation across every compiled constructor of the type. '
                       '"not_established" values are computed and need runtime evidence.'}


def canonical_clause(kind: str, text: str) -> str:
    """Format a registry clause the way the compiler formats source, so that
    redundant parentheses or spacing differences do not count as a mismatch."""
    from gopyt.parser import parse_module
    try:
        mod = parse_module(f'module clause\n\nfn clause() -> bool\n    {kind} {text}\n', 'spec/clause.gopyt', 'spec')
        item = mod.items[0]
        return _clause_text(item.sig.contracts[0])
    except Exception:
        return _normalize(kind + ' ' + text)


def _clause_status(rep: dict, facts: dict, declarations: dict) -> dict:
    wanted = canonical_clause(rep['kind'], rep['text'])
    function = rep['function']
    clauses = [c for c in facts['contracts'] if c['function'] == function and c['kind'] == rep['kind']]
    if function not in {_base_symbol(k) for k in facts['functions']} and not clauses:
        return {'status': 'missing_function', 'function': function}
    for c in clauses:
        if c['text'] == wanted:
            return {'status': 'bound', 'function': function, 'file': c['file'], 'line': c['line']}
    return {'status': 'missing_clause', 'function': function,
            'present_clauses': [c['text'] or f'open {c["open"]}' for c in clauses]}


def _reached_by_tests(facts: dict, tests: dict, functions: set[str]) -> list[dict]:
    """Which current passing tests statically reach the obligated functions.

    This is call-closure over compiled edges, not runtime instrumentation: a
    test whose body guards the call behind a branch it never takes still
    reaches the function here. Actual execution stays unknown.
    """
    reached_by = []
    for name, case in tests.get('cases', {}).items():
        reach = {_base_symbol(k) for k in _reach(facts['edges'], name)}
        hit = sorted(functions & reach)
        if hit:
            reached_by.append({'test': name, 'status': case['status'], 'reaches': hit})
    return reached_by


def obligation_report(root: Path, test_receipts: list[dict], acceptance_paths: list[Path],
                      repo_root: Path | None = None) -> dict:
    root = Path(os.path.abspath(root))
    repo_root = Path(os.path.abspath(repo_root)) if repo_root else Path(__file__).resolve().parent.parent
    result = {'schema': REPORT_SCHEMA, 'root': str(root), 'registry': None, 'contract_document': None,
              'current': None, 'facts': None, 'evidence': {'test_receipts': [], 'acceptance': []},
              'obligations': [], 'errors': [], 'exit_code': 1,
              'meaning': 'Per-obligation verification scope bound to the current inputs. '
                         'No status here is a proof; "runtime_contract" is enforced only on '
                         'executed calls, "test" only on the inputs the test used, "acceptance" '
                         'only on the corpus it ran. Test reach and impact use static call '
                         'closure, not runtime instrumentation. An acceptance report counts as '
                         'passing only with a versioned schema, a suite identity, complete '
                         'current package and compiler coverage, and consistent outcomes.'}
    try:
        registry = load_registry(root, strict=False)
    except (RegistryError, ValueError) as exc:
        result['errors'].append({'kind': 'registry', 'message': str(exc)})
        return _finish(result)
    result['registry'] = {'sha256': registry['_sha256'], 'count': len(registry['obligations']),
                          'invalid_rows': registry['_row_errors']}
    for ident, problems in registry['_row_errors'].items():
        result['errors'].append({'kind': 'registry_row', 'id': ident, 'message': '; '.join(problems)})
    result['contract_document'] = _contract_binding(root, registry)
    contract_status = result['contract_document'].get('status')
    contract_stale = contract_status == 'stale'
    contract_path = result['contract_document'].get('path')
    contract_unverifiable = contract_status in ('unbound', 'missing')
    current = create_receipt(root, 'context')
    result['current'] = {'source': current['source'], 'engine': current['engine'],
                         'compile': current['compile'], 'artifact_sha256': current['artifact_sha256'],
                         'public_declarations': (current['context'] or {}).get('public_declarations', [])}
    if current['compile']['status'] != 'passed':
        result['errors'].append({'kind': 'compile', 'message': current['compile']['diagnostic'] or current.get('error')})
        return _finish(result)
    facts = compute_facts(root)
    result['facts'] = facts
    declarations = {row['id']: row for row in current['context']['public_declarations']}
    bound_tests = [bind_test_receipt(record, current) for record in test_receipts]
    result['evidence']['test_receipts'] = [{k: v for k, v in b.items() if k != 'cases'} | {'case_count': len(b['cases'])}
                                           for b in bound_tests]
    live_tests = {'cases': {}}
    stale_cases = set()
    halted = set()
    for b in bound_tests:
        if b['status'] == 'current':
            live_tests['cases'].update(b['cases'])
            if b['test_status'] in ('failed', 'error'):
                halted.update(set(b['planned']) - set(b['cases']))
        else:
            stale_cases.update(b['cases'])
    # Current receipts that disagree about the same test are a contradiction,
    # not something argument order should decide (challenge P1).
    contradictory = set()
    for name in live_tests['cases']:
        outcomes = {b['cases'][name].get('status') for b in bound_tests
                    if b['status'] == 'current' and name in b['cases']}
        if len(outcomes) > 1:
            contradictory.add(name)
    for name in contradictory:
        live_tests['cases'][name] = {'name': name, 'status': 'contradictory', 'trap': None}
    result['evidence']['contradictory_tests'] = sorted(contradictory)
    acceptance = [bind_acceptance(p, root, current, repo_root) for p in acceptance_paths]
    result['evidence']['acceptance'] = acceptance
    known_functions = {_base_symbol(k) for k in facts['functions']} | {c['function'] for c in facts['contracts']}
    for row in registry['obligations']:
        entry = {'id': row['id'], 'title': row['title'], 'authority': row['authority'],
                 'source': row.get('source'), 'functions': row.get('functions', []),
                 'state_fields': row.get('state_fields', []), 'scopes': {}, 'gaps': [], 'errors': []}
        if contract_stale:
            # The registry was written against the old prose; applicability of
            # every obligation, not just its evidence, is under review.
            entry['applicability'] = 'stale'
        elif contract_unverifiable and row['authority'] == 'contract':
            # A contract-authority claim cannot be checked against a document
            # that is absent or was never cited.
            entry['applicability'] = 'unbound'
        else:
            entry['applicability'] = 'current'
        for fn in entry['functions']:
            if fn not in known_functions:
                entry['errors'].append(f'unknown function {fn}: not in the compiled program')
        functions = set(entry['functions'])
        static, runtime, tests, accepted, prose = [], [], [], [], []
        for rep in row['representations']:
            kind = rep['kind']
            if kind in ('requires', 'ensures'):
                status = _clause_status(rep, facts, declarations)
                instantiated = rep['function'] in {_base_symbol(k) for k in facts['functions']}
                static.append({'kind': kind, **status, 'instantiated': instantiated, 'text': rep['text']})
                if status['status'] == 'bound' and instantiated:
                    runtime.append({'kind': kind, 'function': rep['function'],
                                    'enforced': 'every executed call in this build traps on violation; a call that '
                                                'never takes the violating branch establishes nothing about it',
                                    'reached_by_passing_tests': [t['test'] for t in
                                                                 _reached_by_tests(facts, live_tests, {rep['function']})
                                                                 if t['status'] == 'passed'],
                                    'reach_basis': 'static call closure from current passing tests; whether a call '
                                                   'actually executed at runtime is not instrumented and stays unknown'})
            elif kind == 'preserved_field':
                static.append({'kind': kind, 'type': rep['type'], 'field': rep['field'], **_preserved_field(rep, facts)})
            elif kind == 'test':
                name = rep['test']
                exists = name in facts['functions']
                case = live_tests['cases'].get(name)
                reach = {_base_symbol(k) for k in _reach(facts['edges'], name)} if exists else set()
                reaches = sorted(functions & reach)
                outcome = case['status'] if case else ('stale_receipt' if name in stale_cases else
                                                       'not_run_after_earlier_trap' if name in halted else 'not_run')
                item = {'test': name, 'exists': exists, 'outcome': outcome,
                        'reaches_obligated_functions': reaches}
                if case and case.get('trap') is not None:
                    item['trap'] = case['trap']
                    item['trap_meaning'] = TRAP_MEANING.get(case['trap'], 'runtime trap')
                if exists and functions and not reaches:
                    item['warning'] = 'test never calls an obligated function: it cannot verify this obligation'
                tests.append(item)
            elif kind == 'acceptance':
                # Bind by suite identity, not file basename: two suites can both
                # produce a report.json, and only identity says which corpus ran.
                # The same file supplied twice is one report, not an ambiguity.
                suite, named = rep.get('suite'), rep.get('report')
                matches, seen_paths = [], set()
                for a in acceptance:
                    if suite is not None:
                        if a.get('suite') != suite:
                            continue
                    elif named is not None:
                        try:
                            if Path(a.get('path', '')).resolve() != (root / named).resolve():
                                continue
                        except OSError:
                            continue
                    else:
                        continue
                    if a.get('path') in seen_paths:
                        continue
                    seen_paths.add(a.get('path'))
                    matches.append(a)
                accepted.append({'suite': suite, 'report': named,
                                 'ambiguous': len(matches) > 1,
                                 'bound': matches or [{'status': 'not_supplied'}]})
            elif kind == 'prose':
                prose.append(rep.get('text') or row.get('prose'))
        entry['scopes'] = {'static': static, 'runtime_contract': runtime, 'tests': tests,
                           'acceptance': accepted, 'prose_only': prose}
        entry['gaps'] = _gaps(row, static, runtime, tests, accepted)
        if contract_stale:
            entry['gaps'] = [f'requirement applicability stale: {contract_path} changed after the registry was '
                             f'written; re-review this obligation against the current text'] + entry['gaps']
        elif entry['applicability'] == 'unbound':
            reason = ('the registry cites no authoritative prose document'
                      if contract_status == 'unbound'
                      else f'the cited contract document {contract_path} is missing')
            entry['gaps'] = [f'contract authority unverified: {reason}; this obligation cannot be checked '
                             f'against the prose it claims'] + entry['gaps']
        entry['summary'] = _summary(row, entry)
        result['obligations'].append(entry)
    return _finish(result)


def _gaps(row, static, runtime, tests, accepted) -> list[str]:
    gaps = []
    if row['authority'] == 'unresolved':
        return ['policy decision required; nothing executable may represent this until decided']
    if row['authority'] == 'proposed':
        gaps.append('proposed policy, not part of the current application contract')
    for s in static:
        if s.get('status') in ('missing_clause', 'missing_function'):
            gaps.append(f'{s["kind"]} clause not present in the checked spec ({s.get("status")})')
        if s.get('status') == 'bound' and s.get('instantiated') is False:
            gaps.append(f'{s["kind"]} on {s["function"]} is bound in the spec but has no instantiation in the '
                        f'compiled program: the clause is neither compiled nor enforced in this build')
        if s.get('status') == 'violated':
            gaps.append(f'preserved_field {s["field"]} violated by {[v["function"] for v in s["violations"]]}')
        if s.get('status') == 'no_constructors':
            gaps.append(f'preserved_field {s["field"]}: no compiled constructor writes this field (check type/field names)')
        if s.get('status') == 'not_established':
            gaps.append(f'preserved_field {s["field"]} not established syntactically for '
                        f'{[v["function"] for v in s["not_syntactic"]]}')
        if s.get('unknown_except'):
            gaps.append(f'preserved_field {s["field"]}: except names no compiled function '
                        f'{s["unknown_except"]} (stale exemption list)')
    for r in runtime:
        if not r['reached_by_passing_tests']:
            gaps.append(f'{r["kind"]} on {r["function"]} is compiled but no current passing test reaches it '
                        f'(reachability is static; runtime execution unknown)')
    for t in tests:
        if not t['exists']:
            gaps.append(f'test {t["test"]} does not exist')
        elif t['outcome'] != 'passed':
            gaps.append(f'test {t["test"]} outcome is {t["outcome"]} against current inputs')
        elif t.get('warning'):
            gaps.append(f'test {t["test"]} does not reach an obligated function')
    for a in accepted:
        if a.get('ambiguous'):
            gaps.append(f'acceptance {a["suite"]}: suite identity binds {len(a["bound"])} distinct reports; the '
                        f'identity string does not identify a corpus')
        for b in a['bound']:
            if b['status'] != 'passed':
                gaps.append(f'acceptance {a["suite"]}: {b["status"]}')
    if not static and not tests and not accepted:
        gaps.append('no executable representation: prose only, unverified')
    return gaps


def _summary(row, entry) -> str:
    established = []
    for s in entry['scopes']['static']:
        if s.get('status') == 'bound':
            if s.get('instantiated') is False:
                established.append(f'{s["kind"]} typechecked only (no instantiation in this build)')
            else:
                established.append(f'{s["kind"]} typechecked and compiled')
        elif s.get('status') == 'holds':
            established.append(f'field {s["field"]} syntactically preserved by all constructors')
    for r in entry['scopes']['runtime_contract']:
        n = len(r['reached_by_passing_tests'])
        established.append(f'{r["kind"]} enforced at runtime when called; statically reached by {n} passing '
                           f'current test(s); whether a call actually executed, and branch coverage, are unknown')
    for t in entry['scopes']['tests']:
        if t['outcome'] == 'passed' and t['reaches_obligated_functions']:
            established.append(f'test {t["test"]} passed against current inputs')
    for a in entry['scopes']['acceptance']:
        for b in a['bound']:
            if b['status'] == 'passed':
                size = b.get('scenarios_total')
                suffix = (f' ({size} scenario)' if size == 1 else f' ({size} scenarios)') \
                    if isinstance(size, int) else ''
                established.append(f'acceptance {a["suite"]} passed and is bound to current inputs{suffix}')
    head = 'established: ' + ('; '.join(established) if established else 'nothing')
    return head + ' | gaps: ' + ('; '.join(entry['gaps']) if entry['gaps'] else 'none recorded')


def _finish(result: dict) -> dict:
    blocking = bool(result['errors']) or any(e['errors'] for e in result['obligations'])
    result['exit_code'] = 1 if blocking else 0
    evidence = result.get('evidence', {})
    stale_evidence = []
    contract = result.get('contract_document') or {}
    if contract.get('status') == 'stale':
        stale_evidence.append({'kind': 'contract_document', 'status': 'stale',
                               'path': contract.get('path'),
                               'recorded_sha256': contract.get('recorded_sha256'),
                               'current_sha256': contract.get('current_sha256'),
                               'meaning': 'the registry was written against a different prose contract; '
                                          'every obligation applicability is marked stale'})
    stale_evidence += [r for r in evidence.get('test_receipts', []) if r['status'] != 'current']
    stale_evidence += [a for a in evidence.get('acceptance', [])
                       if a['status'] in ('stale', 'unbound', 'invalid', 'partial', 'contradictory')]
    result['stale_evidence'] = stale_evidence
    result['totals'] = {
        'obligations': len(result['obligations']),
        'with_gaps': sum(1 for e in result['obligations'] if e['gaps']),
        'unresolved': sum(1 for e in result['obligations'] if e['authority'] == 'unresolved'),
        'proposed': sum(1 for e in result['obligations'] if e['authority'] == 'proposed'),
    }
    result['report_sha256'] = digest(canonical(result))
    return result


def compute_facts(root: Path) -> dict:
    """Compile a copied source graph and derive semantic facts from checked nodes."""
    import tempfile
    root = Path(os.path.abspath(root))
    captures, _identity = capture_graph(root)
    with tempfile.TemporaryDirectory(prefix='gopyt-facts-') as directory:
        staged = materialize(captures, root, Path(directory))
        pkg = load_package(str(staged))
        prog = check_package(pkg)
        return semantic_facts(pkg, prog)


# ---------------------------------------------------------------- impact

def change_impact(root: Path, before: dict, test_receipts: list[dict], acceptance_paths: list[Path],
                  repo_root: Path | None = None) -> dict:
    """Compare a previous obligation report with the current tree; expand context conservatively."""
    if before.get('schema') != REPORT_SCHEMA or before.get('facts') is None:
        raise ValueError('impact needs a previous successful obligation report as --before')
    after = obligation_report(root, test_receipts, acceptance_paths, repo_root)
    if after['facts'] is None:
        return {'schema': IMPACT_SCHEMA, 'after': after, 'errors': after['errors'], 'exit_code': 1}
    old_inv = before['current']['source']['packages']
    new_inv = after['current']['source']['packages']
    changed_files = sorted({f'{pkg}/{name}' for pkg in set(old_inv) | set(new_inv)
                            for name in set(old_inv.get(pkg, {})) | set(new_inv.get(pkg, {}))
                            if old_inv.get(pkg, {}).get(name) != new_inv.get(pkg, {}).get(name)})
    old_f, new_f = before['facts']['functions'], after['facts']['functions']
    changed_bodies = sorted(k for k in set(old_f) | set(new_f)
                            if (old_f.get(k) or {}).get('body_sha256') != (new_f.get(k) or {}).get('body_sha256')
                            or (old_f.get(k) or {}).get('parameters') != (new_f.get(k) or {}).get('parameters')
                            or (old_f.get(k) or {}).get('returns') != (new_f.get(k) or {}).get('returns'))
    old_c = {(c['function'], c['kind'], c['text'], c['open']) for c in before['facts']['contracts']}
    new_c = {(c['function'], c['kind'], c['text'], c['open']) for c in after['facts']['contracts']}
    changed_contracts = sorted({c[0] for c in old_c ^ new_c})
    # Field-level write changes. A changed body conservatively changes every
    # field it writes in either version: the syntactic source category
    # (`computed` vs `computed`) cannot show that an arithmetic expression
    # changed, so only the superset is sound.
    def _writes(index: dict, key: str) -> set:
        return {(w['type'], w['field']) for w in (index.get(key) or {}).get('writes', [])}

    changed_fields = set()
    for k in changed_bodies:
        changed_fields |= _writes(old_f, k) | _writes(new_f, k)
    coupling = {(c['type'], c['field']): c for c in after['facts']['field_coupling']}
    seeds = set(changed_bodies) | set(changed_contracts)

    def _readers(fields: set) -> set:
        out = set()
        for field_key in fields:
            out |= set(coupling.get(field_key, {}).get('readers', ()))
        return out

    # Writers reached through helpers count too: a changed helper makes every
    # caller affected, and anything an affected function writes may differ, so
    # those fields' readers join the affected set. Iterate to a fixed point
    # rather than inferring completeness from a smaller selected footprint.
    coupled_readers = _readers(changed_fields)
    affected = _reverse_reach(after['facts']['edges'], seeds | coupled_readers)
    while True:
        wider = _readers({field for k in affected for field in _writes(old_f, k) | _writes(new_f, k)})
        coupled_readers |= wider
        grown = _reverse_reach(after['facts']['edges'], seeds | coupled_readers)
        if grown == affected:
            break
        affected = grown
    affected_base = {_base_symbol(k) for k in affected}
    obligations = []
    for entry in after['obligations']:
        fns = set(entry['functions'])
        fields = set(entry['state_fields'])
        hit_fn = sorted(fns & affected_base)
        hit_field = sorted(f for t, f in changed_fields if f in fields)
        if hit_fn or hit_field:
            obligations.append({'id': entry['id'], 'title': entry['title'], 'authority': entry['authority'],
                                'via_functions': hit_fn, 'via_fields': hit_field,
                                'summary': entry['summary'], 'gaps': entry['gaps']})
    unknowns = []
    for k in sorted(affected):
        row = new_f.get(k)
        if row and row['effects']:
            unknowns.append({'function': k, 'reason': f'declared effects {row["effects"]}: external behavior not modeled'})
    for e in after['facts']['edges']:
        if e['kind'] == 'http_dispatch' and e['callee'] in affected:
            unknowns.append({'function': e['callee'], 'reason': 'reached by framework HTTP dispatch'})
    generic_decls = [d['id'] for d in after_public(after, root)
                     if re.match(r'^(fn|task|workflow|trait|type|enum) \w+\[', d['declaration'])]
    if generic_decls:
        unknowns.append({'declarations': generic_decls, 'reason': 'generic declarations may have uninstantiated bodies with no edges'})
    # Comparability premises the reader must not have to guess (challenge P3/P5/P6):
    # the before report's engine, its package lineage, and the registry itself.
    before_engine = ((before.get('current') or {}).get('engine') or {}).get('sha256')
    engine_changed = bool(before_engine and before_engine != after['current']['engine']['sha256'])
    if engine_changed:
        unknowns.append({'reason': 'engine identity differs between the before report and the current engine; '
                                   'body and contract digest equality across engine builds is not established'})
    before_root = str(before.get('root', ''))
    foreign_root = bool(before_root and Path(before_root).resolve() != Path(os.path.abspath(root)).resolve())
    if foreign_root:
        unknowns.append({'reason': f'before report was produced for a different package root ({before_root}); '
                                   'change lineage between the reports is not established'})
    before_registry = ((before.get('registry') or {}).get('sha256'))
    after_registry = ((after.get('registry') or {}).get('sha256'))
    registry_changed = bool(before_registry and after_registry and before_registry != after_registry)
    if registry_changed:
        unknowns.append({'reason': 'obligations registry changed between the reports; obligation definitions were '
                                   'added, removed or re-scoped, so the after report alone does not describe what '
                                   'was there before'})
    if not changed_files and not engine_changed and not registry_changed and not foreign_root:
        unknowns.append({'reason': 'no file changed between the reports; nothing is stale by source identity'})
    stale = list(after['stale_evidence'])
    return {'schema': IMPACT_SCHEMA,
            'before_source': before['current']['source']['sha256'],
            'after_source': after['current']['source']['sha256'],
            'changed_files': changed_files,
            'changed_functions': changed_bodies,
            'changed_contracts': changed_contracts,
            'changed_fields': sorted(f'{t}.{f}' for t, f in changed_fields),
            'affected_functions': sorted(affected),
            'affected_via_field_coupling': sorted(coupled_readers - seeds),
            'engine_changed': engine_changed,
            'registry_changed': registry_changed,
            'apis': [d for d in after_public(after, root) if d['module'] in
                     {new_f[k]['module'] for k in affected if k in new_f}],
            'obligations_applying': obligations,
            'stale_evidence': stale,
            'unknowns': unknowns,
            'after': after,
            'exit_code': 0,
            'meaning': 'Affected = reverse call closure of changed/recontracted functions plus readers of '
                       'record fields written by changed or affected functions, iterated to a fixed point. '
                       'Every field a changed function writes counts as changed even when its syntactic '
                       'source class is unchanged, because category equality cannot show that an arithmetic '
                       'expression is the same. This is conservative over compiled edges and syntactic field '
                       'writes; it is not a proof of completeness for effects or external state. Engine, '
                       'lineage and registry differences between the reports are recorded as unknowns, '
                       'never silently absorbed.'}


def after_public(report: dict, root: Path) -> list[dict]:
    return report['current']['public_declarations']


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('report', 'impact'):
        cmd = sub.add_parser(name)
        cmd.add_argument('root', type=Path)
        cmd.add_argument('--test-receipt', type=Path, action='append', default=[])
        cmd.add_argument('--acceptance', type=Path, action='append', default=[])
        cmd.add_argument('--repo-root', type=Path, default=None)
        cmd.add_argument('--output', type=Path, required=True)
        if name == 'impact':
            cmd.add_argument('--before', type=Path, required=True)
    cmd = sub.add_parser('facts')
    cmd.add_argument('root', type=Path)
    cmd.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        with args.output.open('x', encoding='utf-8') as stream:
            if args.command == 'facts':
                try:
                    result = compute_facts(args.root)
                    code = 0
                except CompileError as exc:
                    result = {'schema': FACTS_SCHEMA, 'error': format_diag(exc.diag)}
                    code = 1
            else:
                receipts = [json.loads(p.read_text()) for p in args.test_receipt]
                if args.command == 'report':
                    result = obligation_report(args.root, receipts, args.acceptance, args.repo_root)
                else:
                    before = json.loads(args.before.read_text())
                    result = change_impact(args.root, before, receipts, args.acceptance, args.repo_root)
                code = result['exit_code']
            json.dump(result, stream, indent=2, sort_keys=True)
            stream.write('\n')
        return code
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'obligations: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
