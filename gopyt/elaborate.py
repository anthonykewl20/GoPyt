"""Deterministic, conservative library elaboration. Never generates algorithms."""
import copy
import re
from gopyt.ast_nodes import *
from gopyt.fmt import fmt_module, fmt_sig, fmt_expr, fmt_provide, fmt_item, lead, trail
from gopyt.parser import parse_module
from gopyt.types import Nom, subst


def determined(spec, decl, checker):
    sig = decl.sig
    if sig.name == 'serve' and any(isinstance(i, HttpDecl) for i in spec.items):
        return 'return net.http.serve()'
    if sig.kind != 'fn' or checker is None:
        return None
    typed = checker.sigs[spec.path + '.' + sig.name]
    params = dict(typed.params)
    for contract in sig.contracts:
        expr = contract.expr
        if contract.kind != 'ensures' or not isinstance(expr, BinExpr) or expr.op != '==':
            continue
        if not isinstance(expr.left, NameExpr) or expr.left.parts != ['result']:
            continue
        value = expr.right
        if isinstance(value, NameExpr) and len(value.parts) == 1:
            if params.get(value.parts[0]) == typed.ret:
                return 'return ' + fmt_expr(value)
        if isinstance(value, ConstructExpr) and not value.enum and isinstance(typed.ret, Nom):
            fq = checker.resolve_type_name(spec, value.ty, value.line)
            if fq != typed.ret.name:
                continue
            fields = dict(checker.types[fq].field_types(typed.ret.args))
            if len(value.fields) != len(fields) or len(set(n for n, _ in value.fields)) != len(fields):
                continue
            if all(name in fields and isinstance(arg, NameExpr) and len(arg.parts) == 1
                   and params.get(arg.parts[0]) == fields[name] for name, arg in value.fields):
                return 'return ' + fmt_expr(value, 4)
    return None


def impl_stub(spec, checker=None):
    existing = checker.pkg.impl.get(spec.path) if checker else None
    chunks = ["\n".join(lead(it) + [trail(it, fmt_item(it))]) for it in existing.items] if existing else []
    for it in spec.items:
        if isinstance(it, FnDecl):
            if existing and any(isinstance(x, FnDecl) and x.sig.name == it.sig.name for x in existing.items):
                continue
            body = determined(spec, it, checker) or 'unresolved ' + it.sig.name
            chunks.append(fmt_sig(it.sig) + '\n{\n    ' + body + '\n}')
        elif isinstance(it, ProvideDecl) and checker:
            from gopyt.check import DeclChecker, _find_provide
            if DeclChecker(checker)._compiler_filled(spec, it):
                continue
            if existing and _find_provide(existing, it):
                continue
            fq = checker._resolve_trait_ref(spec, it.trait, it.line)
            trait = checker.traits[fq]
            env = {name: checker.resolve_type(spec, arg, {}) for name, arg in zip(trait.tparams, it.trait_args)}
            members = []
            for member in trait.members:
                params = ', '.join(name + ': ' + subst(ty, env).key for name, ty in member.params)
                tp = '[' + ', '.join(member.tparams) + ']' if member.tparams else ''
                header = f'{member.kind} {member.name}{tp}({params}) -> {subst(member.ret, env).key}'
                if member.effects:
                    header += '\n    effects { ' + ', '.join(sorted(member.effects)) + ' }'
                ident = re.sub(r'(?<!^)(?=[A-Z])', '_', it.trait.rsplit('.', 1)[-1]).lower()
                ident += '_' + re.sub(r'(?<!^)(?=[A-Z])', '_', it.target).lower() + '_' + member.name
                members.append(header + '\n{\n    unresolved ' + ident + '\n}')
            chunks.append(fmt_provide(it) + '\n{\n' + '\n'.join('    '+line for body in members for line in body.splitlines()) + '\n}')
    body = '\n\n'.join(chunks)
    words = set(re.findall(r'[A-Za-z_][A-Za-z0-9_]*', body))
    uses = {}
    for module in (spec, existing):
        if module:
            for use in module.uses:
                uses.setdefault(use.path, set()).update(n for n in use.names if n in words)
    if 'net.http.serve()' in body:
        uses.setdefault('net.http', set()).add('serve')
    # Resolved foreign types in trait members require their own allowlists.
    for path, name in re.findall(r'\b([a-z_][a-z_0-9]*(?:\.[a-z_][a-z_0-9]*)*)\.([A-Z][A-Za-z0-9]*)', body):
        if path != spec.path:
            uses.setdefault(path, set()).add(name)
    lines = ['module ' + spec.path, '']
    for path, names in sorted(uses.items()):
        if names:
            names = sorted(names, key=lambda n: (not n[0].isupper(), n))
            lines.append('use ' + path + ' { ' + ', '.join(names) + ' }')
    source = '\n'.join(lines) + '\n\n' + body + '\n'
    return fmt_module(parse_module(source, 'impl/' + spec.path.replace('.', '/') + '.gopyt', 'impl'))


def test_stub(spec, existing=None):
    module = copy.deepcopy(existing) if existing else Module(path='test.' + spec.path, role='test')
    names = {test.name for test in module.tests}
    wanted = sorted({c.open_id[len('needs_test_'):] for it in spec.items if isinstance(it, FnDecl)
                     for c in it.sig.contracts if c.open_id and c.open_id.startswith('needs_test_')})
    for name in wanted:
        if name not in names:
            parsed = parse_module('module test.' + spec.path + '\n\ntest ' + name + '\n{\n    unresolved ' + name + '\n}\n', 'test/' + spec.path.replace('.', '/') + '.gopyt', 'test')
            module.tests.extend(parsed.tests)
    return fmt_module(module)
