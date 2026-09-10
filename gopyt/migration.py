"""Trusted initial plaintext migration; ordinary reads never use this path."""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import time

from gopyt.files import parent_directory
from gopyt.security_config import MAGIC, OVERHEAD, SecurityError, seal, unseal


def _private(info, *, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_mode & 0o077
            or info.st_uid not in (0, os.geteuid())):
        raise SecurityError('private migration recovery storage required')


@contextmanager
def _recovery_directory(store, backup, deadline):
    path = Path(backup)
    if not path.is_absolute() or path.is_relative_to(Path(store.root)):
        raise SecurityError('absolute recovery path outside package required')
    with parent_directory('/', str(path).lstrip('/')) as (parent, name):
        _private(os.fstat(parent), directory=True)
        stage = '.gopyt-migration-' + hashlib.sha256(name.encode()).hexdigest()
        lockname = stage + '.lock'
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            lock = os.open(lockname, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
        except FileExistsError:
            lock = os.open(lockname, flags, dir_fd=parent)
        try:
            info = os.fstat(lock)
            _private(info)
            if info.st_nlink != 1:
                raise SecurityError('invalid migration recovery lock')
            while True:
                store._lock_remaining(deadline)
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(min(.002, store._lock_remaining(deadline)))
            current = os.stat(lockname, dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise SecurityError('migration recovery lock changed')
            yield parent, name, stage
        finally:
            os.close(lock)


def _validate(store, data, digest, maximum):
    if not data or len(data) > maximum or hashlib.sha256(data).hexdigest() != digest:
        raise SecurityError('migration source digest mismatch')
    db = sqlite3.connect(':memory:')
    try:
        db.execute('PRAGMA trusted_schema=OFF')
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, maximum)
        store._deserialize(data, db)
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise SecurityError('migration source integrity failure')
        if db.execute("SELECT 1 FROM kv WHERE typeof(key) != 'text' OR typeof(value) != 'text' LIMIT 1").fetchone():
            raise SecurityError('invalid migration values')
    finally:
        db.close()
    store._check_context()


def migrate(store, directory, backup, digest, maximum, deadline):
    """Called only under the store lock, with target encryption configured."""
    if store._security is None or os.environ.get('GOPYT_STORE_ANCHOR_DIR'):
        raise SecurityError('migration requires encryption without an enrolled authority')
    with store._snapshot(directory) as (fd, identity):
        original = None
        if fd is not None:
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                original = stream.read(maximum + OVERHEAD + 1)
            if store._identity(os.fstat(fd)) != identity:
                raise SecurityError('migration source changed')
            plain = unseal(original, store._security) if original.startswith(MAGIC) else original
            _validate(store, plain, digest, maximum)
    with _recovery_directory(store, backup, deadline) as (parent, name, stage):
        try:
            recovery = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            if original is None:
                raise SecurityError('migration source and recovery copy missing')
            try:
                stale = os.stat(stage, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                _private(stale)
                if stale.st_nlink != 1:
                    raise SecurityError('invalid migration staging file')
                os.unlink(stage, dir_fd=parent)
            data = seal(plain, store._security)
            pending = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                with os.fdopen(pending, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                store._check_context()
                os.link(stage, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                os.unlink(stage, dir_fd=parent)
                os.fsync(parent)
            finally:
                # The recovery lock serializes ownership of this reserved stage.
                try:
                    os.unlink(stage, dir_fd=parent)
                except FileNotFoundError:
                    pass
        else:
            try:
                info = os.fstat(recovery)
                _private(info)
                if info.st_nlink == 2:
                    staged = os.stat(stage, dir_fd=parent, follow_symlinks=False)
                    if (info.st_dev, info.st_ino) != (staged.st_dev, staged.st_ino):
                        raise SecurityError('unrecognized recovery hard link')
                    os.unlink(stage, dir_fd=parent)
                    info = os.fstat(recovery)
                if info.st_nlink != 1 or not 0 < info.st_size <= maximum + OVERHEAD:
                    raise SecurityError('invalid migration recovery file')
                with os.fdopen(recovery, 'rb', closefd=False) as stream:
                    data = stream.read(maximum + OVERHEAD + 1)
                now = os.fstat(recovery)
                if (now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns, now.st_ctime_ns) != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns):
                    raise SecurityError('migration recovery changed')
                _validate(store, unseal(data, (store._security[0].active, store._security[1],
                                               store._security[2])), digest, maximum)
                os.fsync(parent)  # Complete an interrupted recovery-copy admission.
            finally:
                os.close(recovery)
    store._check_context()
    store._publish(directory, data)
    return {'operation': 'migrate', 'status': 'committed', 'source_digest': digest,
            'ciphertext_digest': hashlib.sha256(data).hexdigest()}
