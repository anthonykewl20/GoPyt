"""Operator-only enrollment, inspection and authorized encrypted snapshot restore."""
import argparse
import json
import os
import sys

from gopyt.files import regular_file
from gopyt.security_config import OVERHEAD
from gopyt.storage import MAX_BYTES, Store, StorageError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='application package root')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('enroll', help='enroll once; stop the application first')
    commands.add_parser('status', help='inspect authority; does not validate snapshot availability')
    restore = commands.add_parser('restore', help='publish an authenticated backup as a new generation')
    restore.add_argument('--backup', required=True)
    restore.add_argument('--expected-generation', required=True, type=int)
    restore.add_argument('--reason', required=True)
    args = parser.parse_args(argv)
    store = Store(args.root)
    try:
        if args.command == 'enroll':
            store.enroll_anchor()
            result = store.anchor_status()
        elif args.command == 'status':
            result = store.anchor_status()
        else:
            path = os.path.abspath(args.backup)
            with regular_file('/', path.lstrip('/')) as stream:
                backup = stream.read(MAX_BYTES + OVERHEAD + 1)
            result = store.restore_anchor(backup, expected_generation=args.expected_generation, reason=args.reason)
    except (StorageError, OSError) as error:
        print('storage maintenance failed: ' + str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
