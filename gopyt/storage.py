"""Durable package-local string KV, serialized across cooperating processes.

SQLite operates on a bounded in-memory snapshot. Descriptor-relative I/O owns
the persistent file, so SQLite cannot follow symlinks or create unsafe sidecars.
Whole-file replacement is deliberately simple; this is not a scalable DB server.
"""
from collections import OrderedDict
from contextlib import contextmanager
import fcntl
import os
import secrets
import sqlite3
import stat
import tempfile
import threading
import time
import weakref

from gopyt.files import parent_directory
from gopyt.security_config import storage_cipher,seal,unseal,SecurityError,OVERHEAD

MAX_BYTES = 64 * 1024 * 1024
LOCK_TIMEOUT = 5.0
DIRECTORY = '.gopyt-state'
DATABASE = 'store.sqlite3'
CACHE_ENTRIES = 256
CACHE_BYTES = 1024 * 1024
MAX_BATCH_KEYS = 256
MAX_BATCH_BYTES = 8 * 1024 * 1024


class StorageError(Exception):
    pass


def _open_lock(directory, deadline):
    """Open an existing lock or create it exclusively, tolerating creation races."""
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    while True:
        try:
            return os.open('lock', flags, dir_fd=directory)
        except FileNotFoundError:
            try:
                return os.open('lock', flags | os.O_CREAT | os.O_EXCL,
                               0o600, dir_fd=directory)
            except (FileExistsError, FileNotFoundError):
                # Never follow a replaced directory or wait without a deadline.
                if os.fstat(directory).st_nlink == 0 or time.monotonic() >= deadline:
                    raise StorageError('database lock creation failed')
                time.sleep(0.002)


class Store:
    def __init__(self, root=None):
        self.root = os.path.abspath(root) if root is not None else None
        self._temporary = None
        self._operation_lock = threading.Lock()
        self._pid = os.getpid()
        # Plain Python values only: no retained SQLite connections or file FDs.
        # A timed local lock serializes cache bookkeeping and temporary-root setup.
        self._cache = OrderedDict()
        self._cache_identity = None
        self._cache_bytes = 0
        self._security = None
        self._security_identity = None

    def _clear_cache(self):
        self._cache.clear()
        self._cache_identity = None
        self._cache_bytes = 0

    def _remember(self, key, value):
        size = len(key.encode('utf-8')) + (len(value.encode('utf-8')) if value is not None else 0)
        if size > CACHE_BYTES or CACHE_ENTRIES <= 0:
            return
        previous = self._cache.pop(key, None)
        if previous is not None:
            self._cache_bytes -= previous[1]
        while self._cache and (len(self._cache) >= CACHE_ENTRIES or self._cache_bytes + size > CACHE_BYTES):
            _, (_, previous_size) = self._cache.popitem(last=False)
            self._cache_bytes -= previous_size
        self._cache[key] = (value, size)
        self._cache_bytes += size

    @contextmanager
    def _locked(self, deadline):
        if self.root is None:
            self._temporary = tempfile.TemporaryDirectory(prefix='gopyt-store-')
            # Host temporary roots may contain aliases (macOS /var). Only
            # resolve this newly created private root, never a package root.
            self.root = os.path.realpath(self._temporary.name)
            weakref.finalize(self, self._temporary.cleanup)
        with parent_directory(self.root, DIRECTORY) as (root, name):
            try:
                os.mkdir(name, 0o700, dir_fd=root)
                os.fsync(root)
            except FileExistsError:
                pass
            directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        try:
            lock = _open_lock(directory, deadline)
            try:
                info = os.fstat(lock)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise StorageError('invalid database lock')
                while True:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise StorageError('database busy')
                        time.sleep(0.002)
                # An uncooperative unlink/replacement must not split our lock.
                current = os.stat('lock', dir_fd=directory, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                    raise StorageError('database lock changed')
                for stale in os.listdir(directory):
                    suffix = stale.removeprefix('.pending-')
                    if stale.startswith('.pending-') and len(suffix) == 24 and all(c in '0123456789abcdef' for c in suffix):
                        os.unlink(stale, dir_fd=directory)
                yield directory
            finally:
                os.close(lock)
        finally:
            os.close(directory)

    @staticmethod
    def _identity(info):
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_size <= 0 or info.st_size > MAX_BYTES + OVERHEAD):
            raise StorageError('invalid database file')
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    @contextmanager
    def _snapshot(self, directory):
        try:
            fd = os.open(DATABASE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            yield None, None
            return
        try:
            yield fd, self._identity(os.fstat(fd))
        finally:
            os.close(fd)

    def _load(self, fd, identity, db):
        if fd is None:
            db.execute('CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID')
            return
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(MAX_BYTES + OVERHEAD + 1)
        if not data or len(data) > MAX_BYTES + OVERHEAD or self._identity(os.fstat(fd)) != identity:
            raise StorageError('database changed during read')
        data = unseal(data, self._security)
        if len(data) > MAX_BYTES:
            raise StorageError('database size limit exceeded')
        db.deserialize(data)
        # Reject unexpected tables, views, triggers and indexes before queries.
        schema = db.execute("SELECT type, name, sql FROM sqlite_schema ORDER BY name").fetchall()
        if schema != [('table', 'kv', 'CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID')]:
            raise StorageError('invalid database schema')

    def _save(self, directory, db):
        data = db.serialize()
        if len(data) > MAX_BYTES:
            raise StorageError('database size limit exceeded')
        data = seal(data, self._security)
        temporary = '.pending-' + secrets.token_hex(12)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, DATABASE, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass

    def _operate(self, operation, key, value=None, expected=None):
        if self._pid != os.getpid():
            # A parent thread may have owned the operation lock at fork. Plain
            # cached values own no inherited SQLite connection or descriptor.
            self._operation_lock = threading.Lock()
            self._clear_cache()
            self._pid = os.getpid()
        deadline = time.monotonic() + LOCK_TIMEOUT
        if not self._operation_lock.acquire(timeout=LOCK_TIMEOUT):
            raise StorageError('database busy')
        try:
            return self._operate_locked(operation, key, value, expected, deadline)
        finally:
            self._operation_lock.release()

    def _operate_locked(self, operation, key, value, expected, deadline):
        try:
            from gopyt.capabilities import database_authority
            authority = database_authority(self.root)
            keys = (key if operation == 'get_many' else tuple(row[0] for row in key)
                    if operation == 'compare_exchange_many' else (key,))
            if authority is not None and not authority.permits(
                    keys, read=operation != 'put', write=operation not in ('get', 'get_many')):
                raise StorageError('database authority denied')
            self._security = storage_cipher(self.root)
            identity = None if self._security is None else self._security[2]
            if identity != self._security_identity:
                self._clear_cache()
                self._security_identity = identity
            # A single input must not allocate an unbounded SQLite snapshot.
            inputs = (key, value, expected) if operation not in ('get_many', 'compare_exchange_many') else ()
            if any(len(s) > MAX_BYTES // 2 or len(s.encode('utf-8')) > MAX_BYTES // 2
                   for s in inputs if s is not None):
                raise StorageError('database input size limit exceeded')
            with self._locked(deadline) as directory:
                # Writes always begin from disk, and any failure (including an
                # ambiguous post-rename fsync failure) leaves no cached result.
                if operation not in ('get', 'get_many'):
                    self._clear_cache()
                with self._snapshot(directory) as (fd, identity):
                    if identity != self._cache_identity:
                        self._clear_cache()
                        self._cache_identity = identity
                    if operation == 'get' and identity is not None and key in self._cache:
                        self._cache.move_to_end(key)
                        return self._cache[key][0]
                    if (operation == 'get_many' and identity is not None
                            and all(item in self._cache for item in key)):
                        cached = [self._cache[item][0] for item in key]
                        if sum(len(v.encode('utf-8')) for v in cached if v is not None) > MAX_BATCH_BYTES:
                            raise StorageError('database batch result size limit exceeded')
                        for item in key:
                            self._cache.move_to_end(item)
                        return cached
                    db = sqlite3.connect(':memory:')
                    try:
                        if not hasattr(db, 'serialize') or not hasattr(db, 'deserialize'):
                            raise StorageError('SQLite snapshot support unavailable')
                        db.execute('PRAGMA trusted_schema=OFF')
                        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_BYTES)
                        self._load(fd, identity, db)
                        page_size = db.execute('PRAGMA page_size').fetchone()[0]
                        db.execute(f'PRAGMA max_page_count={MAX_BYTES // page_size}')
                        if operation == 'rekey':
                            if fd is None or self._security is None:
                                raise StorageError('rekey requires an existing authenticated store')
                            self._save(directory, db)
                            return True
                        if operation in ('get_many', 'compare_exchange_many'):
                            keys = key if operation == 'get_many' else tuple(row[0] for row in key)
                            current, result_bytes = [], 0
                            for item in keys:
                                row = db.execute('SELECT value FROM kv WHERE key=?', (item,)).fetchone()
                                if row is not None and not isinstance(row[0], str):
                                    raise StorageError('invalid database value')
                                if operation == 'get_many' and row is not None:
                                    result_bytes += len(row[0].encode('utf-8'))
                                    if result_bytes > MAX_BATCH_BYTES:
                                        raise StorageError('database batch result size limit exceeded')
                                current.append(None if row is None else row[0])
                            if operation == 'get_many':
                                if identity is not None:
                                    for item, found in zip(keys, current):
                                        self._remember(item, found)
                                return current
                            # Every condition is evaluated against one locked snapshot,
                            # before any mutation. A conflict publishes nothing.
                            if any(old != change[1] for old, change in zip(current, key)):
                                return False
                            changed = False
                            for old, (item, _, replacement) in zip(current, key):
                                if old == replacement:
                                    continue
                                changed = True
                                if replacement is None:
                                    db.execute('DELETE FROM kv WHERE key=?', (item,))
                                else:
                                    db.execute('INSERT INTO kv VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (item, replacement))
                            if changed:
                                db.commit()
                                self._save(directory, db)
                            return True
                        if operation == 'get':
                            row = db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
                            if row is not None and not isinstance(row[0], str):
                                raise StorageError('invalid database value')
                            value = None if row is None else row[0]
                            if identity is not None:
                                self._remember(key, value)
                            return value
                        if operation == 'put':
                            db.execute('INSERT INTO kv VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))
                        elif expected is None:
                            result = db.execute('INSERT INTO kv VALUES (?, ?) ON CONFLICT(key) DO NOTHING', (key, value))
                            if result.rowcount == 0:
                                return False
                        else:
                            result = db.execute('UPDATE kv SET value=? WHERE key=? AND value=?', (value, key, expected))
                            if result.rowcount == 0:
                                return False
                        db.commit()
                        self._save(directory, db)
                        return True
                    finally:
                        db.close()
        except (OSError, sqlite3.Error, UnicodeError, OverflowError, SecurityError) as exc:
            self._clear_cache()
            raise StorageError('database operation failed') from exc
        except BaseException:
            self._clear_cache()
            raise

    def get(self, key):
        return self._operate('get', key)

    def put(self, key, value):
        self._operate('put', key, value)

    def compare_exchange(self, key, expected, value):
        return self._operate('compare_exchange', key, value, expected)

    @staticmethod
    def _batch(rows, changes=False):
        # Freeze caller-owned containers before taking locks. Iterators are not
        # accepted: materializing an unbounded producer would defeat this limit.
        if not isinstance(rows, (list, tuple)) or not 1 <= len(rows) <= MAX_BATCH_KEYS:
            raise StorageError('database batch requires 1..256 keys')
        frozen, seen, size = [], set(), 0
        for row in rows:
            if changes:
                if not isinstance(row, (list, tuple)) or len(row) != 3:
                    raise StorageError('invalid database change')
                row = tuple(row)
                key, expected, value = row
                strings = row
            else:
                key, strings = row, (row,)
            if not isinstance(key, str) or not key or key in seen:
                raise StorageError('database batch keys must be nonempty and unique')
            seen.add(key)
            for text in strings:
                if text is None:
                    continue
                if not isinstance(text, str):
                    raise StorageError('database batch values must be strings or absent')
                if len(text) > MAX_BATCH_BYTES:
                    raise StorageError('database batch input size limit exceeded')
                try:
                    size += len(text.encode('utf-8'))
                except UnicodeError as exc:
                    raise StorageError('invalid database text') from exc
                if size > MAX_BATCH_BYTES:
                    raise StorageError('database batch input size limit exceeded')
            frozen.append(row)
        return tuple(frozen)

    def get_many(self, keys):
        """Read an ordered, bounded group from one consistent snapshot."""
        return self._operate('get_many', self._batch(keys))

    def compare_exchange_many(self, changes):
        """Atomically compare and replace/delete every (key, expected, value)."""
        return self._operate('compare_exchange_many', self._batch(changes, changes=True))

    def rekey(self):
        """Trusted maintenance only; authenticate then republish using active key."""
        return self._operate('rekey', '*')
