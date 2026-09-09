"""Explicit authenticated-store maintenance; never called by GoPyT programs."""
import argparse
import json
import os
from pathlib import Path
import sys

from gopyt.files import regular_file
from gopyt.security_config import OVERHEAD
from gopyt.storage import MAX_BYTES, Store, StorageError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['rekey', 'enroll', 'status', 'restore'])
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--backup')
    parser.add_argument('--expected-generation', type=int)
    parser.add_argument('--reason')
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        parser.error('existing package root required')
    if args.operation == 'restore':
        if args.backup is None or args.expected_generation is None or args.reason is None:
            parser.error('restore requires --backup, --expected-generation and --reason')
    elif any(value is not None for value in (args.backup, args.expected_generation, args.reason)):
        parser.error('restoration options require restore')
    store = Store(os.path.abspath(args.root))
    try:
        if args.operation == 'rekey':
            store.rekey()
            result = {'operation':'rekey', 'status':'committed'}
        elif args.operation == 'enroll':
            store.enroll_anchor()
            result = store.anchor_status()
        elif args.operation == 'status':
            result = store.anchor_status()
        else:
            path = os.path.abspath(args.backup)
            with regular_file('/', path.lstrip('/')) as stream:
                backup = stream.read(MAX_BYTES + OVERHEAD + 1)
            result = store.restore_anchor(backup, expected_generation=args.expected_generation, reason=args.reason)
    except (StorageError, OSError) as error:
        if args.operation == 'rekey':
            print(json.dumps({'operation':'rekey', 'status':'error',
                              'message':'rekey failed; retain all keys and inspect the store before retrying'}))
            return 1
        print('storage maintenance failed: ' + str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
