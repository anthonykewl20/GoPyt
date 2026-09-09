"""Explicit authenticated-store maintenance; never called by GoPyT programs."""
import argparse
import json
import os
from pathlib import Path
from gopyt.storage import Store, StorageError


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['rekey'])
    parser.add_argument('--root',required=True,type=Path)
    args=parser.parse_args(argv)
    if not args.root.is_dir():parser.error('existing package root required')
    try:
        Store(os.path.abspath(args.root)).rekey()
    except StorageError:
        print(json.dumps({'operation':'rekey','status':'error',
                          'message':'rekey failed; retain all keys and inspect the store before retrying'}))
        return 1
    print(json.dumps({'operation':'rekey','status':'committed'}))
    return 0


if __name__=='__main__':raise SystemExit(main())
