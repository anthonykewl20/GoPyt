"""Serialized source transactions with durable, idempotent undo recovery.

Advisory locks cover cooperating tools. A journal never overwrites bytes that
are neither the recorded before nor after image (external editor conflict).
"""
import base64
import functools
import json
import os
import threading
from contextlib import contextmanager
from gopyt.files import atomic_write, parent_directory, regular_file, segments

LOCAL = threading.local()
LOCK = '.gopyt-transaction.lock'
JOURNAL = '.gopyt-transaction.json'
MAX_JOURNAL = 64 * 1024 * 1024


def read(root, path):
    with regular_file(root, path) as stream:
        return stream.read()


def remove(root, path):
    with parent_directory(root, path) as (fd, name):
        os.unlink(name, dir_fd=fd)
        os.fsync(fd)


def _decode(data):
    record = json.loads(data)
    if record['version'] != 1 or record['state'] not in ('prepared', 'committed'):
        raise OSError('journal format')
    changes = []
    seen = set()
    for row in record['changes']:
        path = row['path']
        parts = segments(path)
        if path in seen or not (path == 'gopyt.lock' or parts[0] in ('spec', 'impl', 'test')):
            raise OSError('journal path')
        seen.add(path)
        before = base64.b64decode(row['before'], validate=True)
        after = base64.b64decode(row['after'], validate=True)
        changes.append((path, before, after))
    return record, changes


def recover(root):
    try:
        with regular_file(root, JOURNAL) as stream:
            raw = stream.read(MAX_JOURNAL + 1)
    except FileNotFoundError:
        return
    if len(raw) > MAX_JOURNAL:
        raise OSError('journal size')
    try:
        record, changes = _decode(raw)
    except (ValueError, KeyError, TypeError) as exc:
        raise OSError('journal format') from exc
    # Check every byte first; do not partially roll back an external edit.
    for path, before, after in changes:
        if read(root, path) not in (before, after):
            raise OSError('concurrent source edit')
    if record['state'] == 'prepared':
        for path, before, after in reversed(changes):
            if read(root, path) != before:
                atomic_write(root, path, before)
    else:
        if any(read(root, path) != after for path, before, after in changes):
            raise OSError('committed source edit')
    remove(root, JOURNAL)


@contextmanager
def guard(root):
    import fcntl
    from gopyt.diag import CompileError, Diag
    from gopyt.manifest import reject_symlinks

    root = os.path.abspath(root)
    held = getattr(LOCAL, 'held', None)
    if held is None:
        held = LOCAL.held = {}
    if root in held:
        yield
        return
    reject_symlinks(root)
    try:
        with parent_directory(root, LOCK) as (directory, name):
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        try:
            import stat
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError('lock type')
            fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if held else 0))
            held[root] = fd
            try:
                recover(root)
                yield
            finally:
                del held[root]
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    except OSError as exc:
        raise CompileError(Diag(46, 'gopyt.toml', 1)) from exc


def guarded(fn):
    @functools.wraps(fn)
    def wrapped(root, *args, **kwargs):
        with guard(root):
            return fn(root, *args, **kwargs)
    return wrapped


def commit(root, changes, writer=atomic_write):
    """Caller holds guard; before/after images contain checked source and lock."""
    if any(read(root, path) != before for path, before, after in changes):
        raise OSError('source changed')
    record = {'version': 1, 'state': 'prepared', 'changes': [
        {'path': path, 'before': base64.b64encode(before).decode(),
         'after': base64.b64encode(after).decode()}
        for path, before, after in changes]}
    data = json.dumps(record, separators=(',', ':')).encode()
    if len(data) > MAX_JOURNAL:
        raise OSError('journal size')
    atomic_write(root, JOURNAL, data)
    try:
        for path, before, after in changes:
            if read(root, path) != before:
                raise OSError('source changed')
            writer(root, path, after)
        record['state'] = 'committed'
        atomic_write(root, JOURNAL, json.dumps(record, separators=(',', ':')).encode())
    except OSError:
        recover(root)
        raise
    remove(root, JOURNAL)
