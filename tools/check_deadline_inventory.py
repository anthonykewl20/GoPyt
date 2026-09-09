"""Check explicit deadline-policy inventory coverage, not runtime behavior."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gopyt.natives import NATIVES, resolve_provide
from gopyt.toolchain import source_fingerprint


def check(path):
    record = json.loads(path.read_text())
    groups = record['groups']
    names = [name for group in groups.values() for name in group['natives']]
    if len(names) != len(set(names)):
        raise ValueError('duplicate native policy assignment')
    actual = {name for name in NATIVES if not name.startswith('provide:')}
    if set(names) != actual:
        raise ValueError('native inventory drift: ' + repr({
            'missing': sorted(actual - set(names)), 'obsolete': sorted(set(names) - actual)}))
    for group in groups.values():
        if not group['policy'] or not group['sources']:
            raise ValueError('missing policy or implementation reference')
        for source in group['sources']:
            if not (ROOT / source).is_file():
                raise ValueError('missing source: ' + source)
    if source_fingerprint(ROOT / 'gopyt').hex() != record['runtime_sha256']:
        raise ValueError('runtime changed; review boundaries before refreshing inventory')
    for name, expected in record['source_sha256'].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError('reviewed source changed: ' + name)
    expected = {'provide:core.convert.Json:Inventory:to_json',
                'provide:core.convert.Json:Inventory:from_json',
                'provide:core.convert.FromStr:Inventory:from_str'}
    if set(record['provide_examples']) != expected:
        raise ValueError('conversion-form inventory mismatch')
    if any(resolve_provide(name) is None for name in expected):
        raise ValueError('documented conversion form no longer resolves')
    print(json.dumps({'fixed_natives': len(names), 'policy_groups': len(groups),
                      'conversion_forms': len(expected), 'runtime_sha256': record['runtime_sha256'],
                      'scope': 'inventory and reviewed-source identity only; not behavioral qualification'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inventory', nargs='?', type=Path,
                        default=ROOT / 'validation/deadline-boundaries/inventory.json')
    args = parser.parse_args()
    try:
        check(args.inventory)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, str(error) + '\n')
