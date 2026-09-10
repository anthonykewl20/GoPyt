#!/usr/bin/env python3
"""Derive and verify the consolidated release component inventory.

Every component identity here is read back from a pinned or retained file in
this repository: the hash-pinned build/security requirements, the pinned
interpreter archives, the immutable action commits used by the workflows, the
verified installed-distribution report and the publisher SBOM documents it
preserved. Nothing is transcribed by hand, so the inventory cannot drift from
the inputs it describes.

This is an inventory and a drift check. It is not a vulnerability scan, a
licensing conclusion, or an independent audit, and a declared component is not
proof that the component was linked into a particular selected artifact.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

INVENTORY = Path('validation/component-review/release-components.json')
INSTALLED = Path('validation/component-inventory/local.json')
PUBLISHER = Path('validation/component-review/python-publisher-downloads.json')
ACTION_PATHS = Path('validation/component-review/action-record-paths.json')
UNRESOLVED = Path('validation/component-review/unresolved-components.json')
INTERPRETERS = Path('requirements/python-standalone.json')
REQUIREMENTS = (Path('requirements/build.txt'), Path('requirements/security.txt'))
WORKFLOWS = Path('.github/workflows')

PIN = re.compile(r'^(?P<name>[A-Za-z0-9._-]+)==(?P<version>[^\s\\]+)')
HASH = re.compile(r'--hash=sha256:([0-9a-f]{64})')
USES = re.compile(r'uses:\s*(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)@(?P<ref>[0-9a-f]{40})')


def _read_json(root, relative):
    return json.loads((root / relative).read_text())


def pinned_requirements(root):
    """Exact name/version/artifact hashes for every hash-pinned build input."""
    found = {}
    for relative in REQUIREMENTS:
        current = None
        for line in (root / relative).read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            match = PIN.match(stripped)
            if match:
                current = {'kind': 'python_distribution',
                           'name': match.group('name'),
                           'version': match.group('version'),
                           'pinned_in': str(relative),
                           'artifact_sha256': []}
                found[match.group('name')] = current
            if current is not None:
                current['artifact_sha256'].extend(HASH.findall(stripped))
    for entry in found.values():
        entry['artifact_sha256'] = sorted(set(entry['artifact_sha256']))
    return found


def pinned_interpreters(root):
    interpreters = {}
    for key, pin in sorted(_read_json(root, INTERPRETERS).items()):
        interpreters[key] = {'kind': 'interpreter_archive',
                             'name': 'cpython',
                             'version': pin['version'],
                             'release': pin['release'],
                             'target': key,
                             'url': pin['url'],
                             'sha256': pin['sha256'],
                             'size': pin['size'],
                             'pinned_in': str(INTERPRETERS)}
    return interpreters


def pinned_actions(root):
    actions = {}
    for path in sorted((root / WORKFLOWS).glob('*.yml')):
        for match in USES.finditer(path.read_text()):
            identity = f"{match.group('owner')}/{match.group('repo')}"
            entry = actions.setdefault(identity, {'kind': 'ci_action',
                                                  'name': identity,
                                                  'commit': match.group('ref'),
                                                  'workflows': []})
            if entry['commit'] != match.group('ref'):
                raise SystemExit(f'{identity} is pinned to two different commits')
            entry['workflows'].append(path.name)
    for entry in actions.values():
        entry['workflows'] = sorted(set(entry['workflows']))
    return actions


def installed_components(root):
    """Vendored pins, bundled metadata and SBOM components of installed inputs."""
    components = {}
    report = _read_json(root, INSTALLED)
    for distribution in report['distributions']:
        origin = f"{distribution['name']} {distribution['version']}"
        for pin in distribution.get('vendor_pins', ()):
            components[f"vendor:{distribution['name']}:{pin['name']}"] = {
                'kind': 'vendored_package', 'name': pin['name'],
                'version': pin['version'], 'inside': origin}
        for bundled in distribution.get('bundled_metadata', ()):
            components[f"bundled:{distribution['name']}:{bundled['name']}"] = {
                'kind': 'bundled_package', 'name': bundled['name'],
                'version': bundled.get('version'), 'inside': origin,
                'license_expression': bundled.get('license_expression')}
        for sbom in distribution.get('sboms', ()):
            for component in sbom['document'].get('components', ()):
                key = f"sbom:{distribution['name']}:{component['name']}@{component.get('version')}"
                components[key] = {
                    'kind': 'native_component', 'name': component['name'],
                    'version': component.get('version'), 'inside': origin,
                    'sbom': sbom['path'],
                    'licenses': _licenses(component),
                    'purl': component.get('purl'),
                    'hashes': {entry['alg']: entry['content']
                               for entry in component.get('hashes', ())}}
    return components


def _licenses(component):
    names = []
    for entry in component.get('licenses', ()):
        licence = entry.get('license', {})
        name = licence.get('id') or licence.get('name') or entry.get('expression')
        if name:
            names.append(name)
    return sorted(set(names))


def interpreter_native_inputs(root):
    """Inputs the interpreter publisher declares, as preserved literals.

    The publisher list covers every platform and tool the publisher builds,
    including many this project never selects. Entries that declare
    `library_names` are linkable libraries; the rest are build tooling. Which
    of them was actually linked into a selected archive is not determinable
    from this data and is recorded as such rather than guessed.
    """
    inputs = {}
    for name, entry in sorted(_read_json(root, PUBLISHER).items()):
        if not isinstance(entry, dict) or 'url' not in entry:
            continue
        inputs[f'interpreter-input:{name}'] = {
            'kind': 'interpreter_native_input',
            'name': name,
            'version': entry.get('actual_version') or entry.get('version'),
            'url': entry['url'],
            'sha256': entry.get('sha256'),
            'licenses': entry.get('licenses') or [],
            'library_names': entry.get('library_names') or [],
            'role': 'library' if entry.get('library_names') else 'build_tool',
            'selected_linkage': 'not determinable from publisher data',
            'declared_in': str(PUBLISHER)}
    return inputs


def derive(root):
    derived = {'python_distributions': pinned_requirements(root),
               'interpreter_archives': pinned_interpreters(root),
               'ci_actions': pinned_actions(root),
               'installed_components': installed_components(root),
               'interpreter_native_inputs': interpreter_native_inputs(root)}
    canonical = json.dumps(derived, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return {'schema': 'gopyt.release-components.v1',
            'scope': ('Every identity is read back from a pinned or retained file in this '
                      'repository. This is an inventory and a drift check, not a '
                      'vulnerability scan, a licensing conclusion or an independent audit.'),
            'sources': sorted(str(path) for path in
                              (*REQUIREMENTS, INTERPRETERS, INSTALLED, PUBLISHER,
                               ACTION_PATHS, WORKFLOWS)),
            'counts': {name: len(value) for name, value in sorted(derived.items())},
            'components': derived,
            'digest': 'sha256:' + hashlib.sha256(canonical).hexdigest()}


def check(root):
    problems = []
    current = derive(root)
    path = root / INVENTORY
    if not path.exists():
        return [f'missing {INVENTORY}; run with --emit']
    retained = json.loads(path.read_text())
    if retained.get('digest') != current['digest']:
        problems.append('retained inventory digest differs from the derived inputs; '
                        're-emit after reviewing the change')
    for name, count in current['counts'].items():
        if retained.get('counts', {}).get(name) != count:
            problems.append(f'{name}: retained {retained.get("counts", {}).get(name)} '
                            f'entries, derived {count}')
    if not current['components']['ci_actions']:
        problems.append('no immutable action commits were found in the workflows')
    for identity, action in sorted(current['components']['ci_actions'].items()):
        if len(action['commit']) != 40:
            problems.append(f'{identity} is not pinned to a full commit')
    unresolved_path = root / UNRESOLVED
    unresolved = {}
    if unresolved_path.exists():
        unresolved = {entry['component']: entry
                      for entry in json.loads(unresolved_path.read_text())['entries']}
    for key, component in sorted(current['components']['installed_components'].items()):
        if component['kind'] != 'native_component' or component['licenses']:
            continue
        # A publisher record without a license field is a known gap, not a pass.
        # It must be retained with its exact identity so a reviewer can resolve
        # it; anything newly undeclared fails here.
        entry = unresolved.get(key)
        if entry is None:
            problems.append(f'{key} has no declared license and is not recorded in '
                            f'{UNRESOLVED}')
        elif entry.get('source_sha256') not in (component['hashes'].get('SHA-256'), None):
            problems.append(f'{key} is recorded against a different source digest')
        elif not entry.get('resolution_required'):
            problems.append(f'{key} is recorded without the resolution it still needs')
    for key, component in sorted(current['components']['interpreter_native_inputs'].items()):
        # Only linkable libraries need a declared license here; build tooling is
        # recorded with its identity but is not linked into a release artifact.
        if component['role'] != 'library' or component['licenses']:
            continue
        if key not in unresolved:
            problems.append(f'{key} declares linkable libraries with no license and is '
                            f'not recorded in {UNRESOLVED}')
    known = (set(current['components']['installed_components'])
             | set(current['components']['interpreter_native_inputs']))
    for key in sorted(set(unresolved) - known):
        problems.append(f'{key} is recorded as unresolved but no longer appears in the '
                        f'derived inventory')
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--emit', action='store_true',
                        help='write the derived inventory instead of checking it')
    args = parser.parse_args(argv)
    if args.emit:
        derived = derive(args.root)
        (args.root / INVENTORY).write_text(json.dumps(derived, indent=2, sort_keys=True) + '\n')
        print('wrote ' + str(INVENTORY))
        return 0
    problems = check(args.root)
    for problem in problems:
        print('release components: ' + problem, file=sys.stderr)
    if problems:
        return 1
    print('ok release component inventory')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
