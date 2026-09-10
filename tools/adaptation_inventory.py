#!/usr/bin/env python3
"""Repository-wide external source reference and adaptation inventory.

Every external repository named anywhere in the tracked tree must be classified
here with what it is to this project, whether any of its code was adopted, and
where its license record lives. The scan is the authority: a reference that is
not classified fails, and a classification for a reference that no longer
appears fails too, so the inventory cannot silently drift from the tree.

A text scan cannot prove the absence of unattributed source. This inventory
records what is referenced and how it was used; it is not a license audit and
not an originality proof.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

INVENTORY = Path('validation/component-review/adaptations.json')
REFERENCE = re.compile(
    r'(?:github\.com|raw\.githubusercontent\.com)/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)')
# GitHub REST and documentation URLs put fixed path segments where an owner and
# repository would otherwise appear. They name no external project.
NOT_A_REPOSITORY = {'repos', 'users', 'orgs', 'advisories', 'en', 'docs', 'search',
                    'apps', 'settings', 'notifications', 'login', 'about', 'features',
                    'security', 'pricing', 'enterprise', 'sponsors', 'marketplace'}
SELF = {'anthonykewl20'}
CLASSIFICATIONS = {'dependency', 'build_input', 'interpreter_input', 'infrastructure',
                   'adapted', 'behavioral_reference', 'conceptual_prior_art'}


def tracked_files(root):
    listing = subprocess.run(['git', '-C', str(root), 'ls-files'],
                             capture_output=True, text=True, check=True)
    return [line for line in listing.stdout.splitlines() if line]


def scan(root):
    """Every external repository reference, mapped to the files naming it."""
    found = {}
    for name in tracked_files(root):
        path = root / name
        try:
            text = path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue
        for owner, repo in REFERENCE.findall(text):
            if owner.lower() in NOT_A_REPOSITORY or owner.lower() in SELF:
                continue
            identity = f'{owner}/{repo.rstrip(".")}'
            found.setdefault(identity, set()).add(name)
    return {identity: sorted(files) for identity, files in sorted(found.items())}


def check(root):
    problems = []
    path = root / INVENTORY
    if not path.exists():
        return [f'missing {INVENTORY}']
    inventory = json.loads(path.read_text())
    recorded = {entry['source']: entry for entry in inventory['entries']}
    found = scan(root)

    for identity in sorted(set(found) - set(recorded)):
        problems.append(f'{identity} is referenced in {found[identity][0]} but is not '
                        f'classified in {INVENTORY}')
    for identity in sorted(set(recorded) - set(found)):
        problems.append(f'{identity} is classified but no longer referenced anywhere; '
                        f'remove it or restore the reference')
    for identity, entry in sorted(recorded.items()):
        if entry.get('classification') not in CLASSIFICATIONS:
            problems.append(f'{identity} has an unknown classification '
                            f'{entry.get("classification")!r}')
        if 'code_adopted' not in entry:
            problems.append(f'{identity} does not state whether code was adopted')
        if entry.get('code_adopted') and not entry.get('license_record'):
            problems.append(f'{identity} adopted code without a license record')
        if entry.get('code_adopted') and not entry.get('revision'):
            problems.append(f'{identity} adopted code without a pinned revision')
        if not entry.get('use'):
            problems.append(f'{identity} does not record what it is used for')
        if entry.get('unresolved') and not entry.get('resolution_required'):
            problems.append(f'{identity} is marked unresolved without naming the '
                            f'resolution it needs')
        if identity in found and entry.get('referenced_in') != found[identity]:
            problems.append(f'{identity} records different referencing files than the scan')
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--scan', action='store_true',
                        help='print the scan result instead of checking the inventory')
    args = parser.parse_args(argv)
    if args.scan:
        print(json.dumps(scan(args.root), indent=2))
        return 0
    problems = check(args.root)
    for problem in problems:
        print('adaptations: ' + problem, file=sys.stderr)
    if problems:
        return 1
    print('ok adaptation inventory')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
