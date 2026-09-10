"""Explicit authenticated-store maintenance; never called by GoPyT programs."""
import argparse
import json
import os
from pathlib import Path
import sys

from gopyt.resource_budget import ResourceBudget, ResourceLimits, ResourceLimitError
from gopyt.resource_descriptors import DescriptorRegistry
from gopyt.storage import Store, StorageError


class MaintenanceContext:
    deadline_ns = None

    def __init__(self, native_bytes, descriptors):
        self.resource_budget = ResourceBudget(ResourceLimits(native_bytes, 0, descriptors, 0))
        self.descriptors = DescriptorRegistry(self.resource_budget)

    def check_cancelled(self):
        pass

    def close(self):
        if not self.descriptors.close():
            raise StorageError('maintenance descriptor cleanup incomplete')


def _limit(value):
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError('limit must be a nonnegative i64 integer')
    if not 0 <= result < (1 << 63):
        raise argparse.ArgumentTypeError('limit must be a nonnegative i64 integer')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['rekey', 'enroll', 'status', 'restore', 'fence-key', 'migrate'])
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--backup')
    parser.add_argument('--expected-generation', type=int)
    parser.add_argument('--reason')
    parser.add_argument('--expected-digest')
    parser.add_argument('--native-bytes', type=_limit, default=256 * 1024 * 1024)
    parser.add_argument('--descriptors', type=_limit, default=256)
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        parser.error('existing package root required')
    if args.operation != 'migrate' and args.expected_digest is not None:
        parser.error('--expected-digest requires migrate')
    if args.operation == 'migrate':
        if (args.backup is None or args.expected_digest is None
                or args.expected_generation is not None or args.reason is not None):
            parser.error('migrate requires only --backup and --expected-digest')
    elif args.operation == 'restore':
        if args.backup is None or args.expected_generation is None or args.reason is None:
            parser.error('restore requires --backup, --expected-generation and --reason')
    elif args.operation == 'fence-key':
        if args.expected_generation is None or args.backup is not None or args.reason is not None:
            parser.error('fence-key requires only --expected-generation')
    elif any(value is not None for value in (args.backup, args.expected_generation, args.reason)):
        parser.error('restoration options require restore')
    context = MaintenanceContext(args.native_bytes, args.descriptors)
    try:
        try:
            store = Store(os.path.abspath(args.root), context=context)
            if args.operation == 'migrate':
                result = store.migrate(backup=args.backup, expected_digest=args.expected_digest)
            elif args.operation == 'rekey':
                store.rekey()
                result = {'operation':'rekey', 'status':'committed'}
            elif args.operation == 'enroll':
                store.enroll_anchor()
                result = store.anchor_status()
            elif args.operation == 'fence-key':
                result = store.fence_key(expected_generation=args.expected_generation)
            elif args.operation == 'status':
                result = store.anchor_status()
            else:
                result = store.restore_anchor_file(args.backup, expected_generation=args.expected_generation, reason=args.reason)
        finally:
            context.close()
    except (StorageError, OSError, ResourceLimitError) as error:
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
