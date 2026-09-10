"""Launcher that installs the isolation profile and then execs the real command.

Setting up a sandbox in a `preexec_fn` runs arbitrary work between fork and exec
in a process that may hold another thread's locks. Doing it in a separate,
isolated interpreter that execs the target keeps that work in a process of its
own, and a failure here is a failed launch rather than an unisolated run.
"""
import ast
import os
import sys

from gopyt.isolation import Limits, enter


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print('one literal payload required', file=sys.stderr)
        return 2
    try:
        payload = ast.literal_eval(argv[0])
        read_only = [str(path) for path in payload['read_only']]
        writable = [str(path) for path in payload.get('writable', ())]
        limits = Limits(**{name: int(value) for name, value in payload['limits'].items()})
        command = [str(part) for part in payload['command']]
    except (ValueError, SyntaxError, KeyError, TypeError) as error:
        print(f'malformed launcher payload: {error}', file=sys.stderr)
        return 2
    try:
        enter(read_only, limits, writable)
    except OSError as error:
        print(f'isolation failed: {error}', file=sys.stderr)
        return 3
    os.execv(command[0], command)


if __name__ == '__main__':
    raise SystemExit(main())
