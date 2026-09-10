"""Durable package-local string KV, serialized across cooperating processes.

SQLite operates on a bounded in-memory snapshot. Descriptor-relative I/O owns
the persistent file, so SQLite cannot follow symlinks or create unsafe sidecars.
Whole-file replacement is deliberately simple; this is not a scalable DB server.
"""
from collections import OrderedDict
from contextlib import contextmanager, ExitStack
import fcntl
import hashlib
import os
import secrets
import sqlite3
import stat
import tempfile
import threading
import time
import weakref
from gopyt.resource_budget import ResourceLimitError
from gopyt.resource_bytes import read_payload, allocate_payload

from gopyt.rollback import locked_anchor, snapshot_digest
from gopyt.files import parent_directory, opened_descriptor, regular_file
from gopyt.security_config import storage_cipher,seal,unseal,unseal_payload,seal_payload,SecurityError,OVERHEAD

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


class StorageAuthorityError(StorageError):
    """The configured database authority refused these keys.

    Typed so a caller can count it as an authorization denial instead of
    matching on a message. It stays a StorageError, so every existing handler
    keeps mapping it to the same typed database error.
    """


class StorageGenerationError(StorageError):
    """A stale or mismatched authority generation refused the operation."""


def _open_lock(directory, deadline, check_context=lambda: None, *, opener=None):
    """Open an existing lock or create it exclusively, tolerating creation races."""
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    opener = os.open if opener is None else opener
    while True:
        check_context()
        try:
            return opener('lock', flags, dir_fd=directory)
        except FileNotFoundError:
            try:
                return opener('lock', flags | os.O_CREAT | os.O_EXCL,
                               0o600, dir_fd=directory)
            except (FileExistsError, FileNotFoundError):
                # Never follow a replaced directory or wait without a deadline.
                if os.fstat(directory).st_nlink == 0 or time.monotonic() >= deadline:
                    raise StorageError('database lock creation failed')
                time.sleep(0.002)


class Store:
    def __init__(self, root=None, *, context=None):
        # The VM owns this store; its context must not retain the entire VM.
        self._context = weakref.proxy(context) if context is not None else None
        self.root = os.path.abspath(root) if root is not None else None
        self._temporary = None
        self._operation_lock = threading.Lock()
        self._pid = os.getpid()
        # Plain Python values only: no retained SQLite connections or file FDs.
        # A timed local lock serializes cache bookkeeping and temporary-root setup.
        self._cache = OrderedDict()
        self._cache_identity = None
        self._cache_bytes = 0
        self._anchor = None
        self._security = None
        self._security_identity = None

    def _check_context(self):
        if self._context is not None:
            self._context.check_cancelled()

    def _lock_remaining(self, deadline):
        self._check_context()
        remaining = deadline - time.monotonic()
        if self._context is not None and self._context.deadline_ns is not None:
            remaining = min(remaining,
                            (self._context.deadline_ns - time.monotonic_ns()) / 1_000_000_000)
        if remaining <= 0:
            self._check_context()
            raise StorageError('database busy')
        return remaining

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
    def _locked(self, deadline, *, enroll=False, restore=False):
        if self.root is None:
            self._temporary = tempfile.TemporaryDirectory(prefix='gopyt-store-')
            # Host temporary roots may contain aliases (macOS /var). Only
            # resolve this newly created private root, never a package root.
            self.root = os.path.realpath(self._temporary.name)
            weakref.finalize(self, self._temporary.cleanup)
        descriptors = getattr(self._context, 'descriptors', None)
        with ExitStack() as owners:
            def acquire(path, flags, mode=0o777, *, dir_fd=None):
                return owners.enter_context(opened_descriptor(
                    path, flags, mode, dir_fd=dir_fd, descriptors=descriptors))
            with parent_directory(self.root, DIRECTORY, descriptors=descriptors) as (root, name):
                try:
                    os.mkdir(name, 0o700, dir_fd=root)
                    os.fsync(root)
                except FileExistsError:
                    pass
                directory = acquire(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            lock = _open_lock(directory, deadline, self._check_context, opener=acquire)
            info = os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise StorageError('invalid database lock')
            while True:
                self._lock_remaining(deadline)
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(min(0.002, self._lock_remaining(deadline)))
            self._check_context()
            # An uncooperative unlink/replacement must not split our lock.
            current = os.stat('lock', dir_fd=directory, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise StorageError('database lock changed')
            with locked_anchor(self.root, lambda: self._lock_remaining(deadline), enroll=enroll, descriptors=descriptors) as anchor:
                if anchor is not None:
                    if self._security is None:
                        raise SecurityError('anchored storage requires encryption')
                    if not restore:
                        anchor.recover(directory, DATABASE, MAX_BYTES + OVERHEAD)
                self._anchor = anchor
                try:
                    stale_names = (() if restore else os.listdir(directory) if descriptors is None
                                   else descriptors.listdir(directory))
                    for stale in stale_names:
                        self._check_context()
                        suffix = stale.removeprefix('.pending-')
                        if stale.startswith('.pending-') and len(suffix) == 24 and all(c in '0123456789abcdef' for c in suffix):
                            os.unlink(stale, dir_fd=directory)
                    self._check_context()
                    yield directory
                finally:
                    self._anchor = None

    @staticmethod
    def _identity(info):
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_size <= 0 or info.st_size > MAX_BYTES + OVERHEAD):
            raise StorageError('invalid database file')
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    @contextmanager
    def _snapshot(self, directory):
        with ExitStack() as owners:
            try:
                fd = owners.enter_context(opened_descriptor(
                    DATABASE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=directory, descriptors=getattr(self._context, 'descriptors', None)))
            except FileNotFoundError:
                yield None, None
                return
            yield fd, self._identity(os.fstat(fd))

    def _load(self, fd, identity, db):
        if fd is None:
            db.execute('CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID')
            return
        budget = getattr(self._context, 'resource_budget', None)
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            if budget is not None:
                with read_payload(stream, budget, MAX_BYTES + OVERHEAD + 1,
                                  self._check_context) as payload:
                    self._load_image(payload.data, fd, identity, db)
            else:
                self._load_image(stream.read(MAX_BYTES + OVERHEAD + 1),
                                 fd, identity, db)

    def _load_image(self, data, fd, identity, db):
        try:
            if not data or len(data) > MAX_BYTES + OVERHEAD or self._identity(os.fstat(fd)) != identity:
                raise StorageError('database changed during read')
            if self._anchor is not None and self._anchor.record is not None:
                if hashlib.sha256(data).hexdigest() != self._anchor.record['digest']:
                    raise SecurityError('snapshot changed after anchor validation')
            self._deserialize_snapshot(data, db)
        finally:
            data = None

    def _deserialize_snapshot(self, data, db):
        plaintext = None
        try:
            budget = getattr(self._context, 'resource_budget', None)
            if budget is None:
                self._deserialize(unseal(data, self._security), db)
            else:
                with unseal_payload(data, self._security, budget) as plaintext:
                    self._deserialize(plaintext, db)
        finally:
            plaintext = None
            data = None

    @staticmethod
    def _deserialize(data, db):
        if len(data) > MAX_BYTES:
            raise StorageError('database size limit exceeded')
        db.deserialize(data)
        # Reject unexpected tables, views, triggers and indexes before queries.
        schema = db.execute("SELECT type, name, sql FROM sqlite_schema ORDER BY name").fetchall()
        if schema != [('table', 'kv', 'CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID')]:
            raise StorageError('invalid database schema')

    @contextmanager
    def _serialized_payload(self, db, budget):
        # The connection is private to this operation. No writes occur between
        # these queries and serialize; both refer explicitly to the main image.
        values = []
        for pragma in ('page_count', 'page_size'):
            cursor = db.execute(f'PRAGMA main.{pragma}')
            try:
                row = cursor.fetchone()
                if row is None or len(row) != 1 or type(row[0]) is not int:
                    raise StorageError('invalid database image size')
                values.append(row[0])
            finally:
                cursor.close()
        pages, page_size = values
        if (pages <= 0 or not 512 <= page_size <= 65536
                or page_size & (page_size - 1)):
            raise StorageError('invalid database image size')
        size = pages * page_size
        if size > MAX_BYTES:
            raise StorageError('database size limit exceeded')
        with allocate_payload(budget, size) as payload:
            # CPython may overlap a SQLite image and a Python bytes result.
            # Admit both in addition to the destination before serialization.
            with budget.reserve(native_bytes=2 * size):
                raw = None
                try:
                    self._check_context()
                    raw = db.serialize(name='main')
                    if len(raw) != size:
                        raise StorageError('database image size changed')
                    with memoryview(payload.data) as destination:
                        destination[:] = raw
                finally:
                    raw = None
            self._check_context()
            yield payload.data

    def _save(self, directory, db, *, restore=None, authorize_writer=False):
        self._check_context()
        budget = getattr(self._context, 'resource_budget', None)
        if budget is None:
            data = db.serialize()
            if len(data) > MAX_BYTES:
                raise StorageError('database size limit exceeded')
            data = seal(data, self._security)
            self._publish(directory, data, restore=restore, authorize_writer=authorize_writer)
        else:
            data = ciphertext = None
            try:
                with self._serialized_payload(db, budget) as data:
                    with seal_payload(data, self._security, budget) as ciphertext:
                        self._publish(directory, ciphertext, restore=restore,
                                      authorize_writer=authorize_writer)
            finally:
                data = ciphertext = None

    def _publish(self, directory, data, *, restore=None, authorize_writer=False):
        self._check_context()
        temporary = '.pending-' + secrets.token_hex(12)
        owners = ExitStack()
        fd = owners.enter_context(opened_descriptor(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=directory,
            descriptors=getattr(self._context, 'descriptors', None)))
        admitted = False
        try:
            with owners:
                with os.fdopen(fd, 'wb', closefd=False) as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            # Publication admission: cancellation after entering replace cannot
            # undo the rename or abandon the directory durability barrier.
            self._check_context()
            if self._anchor is not None:
                os.fsync(directory)  # Recovery staging must survive authority advancement.
                self._check_context()
                admitted = True  # Even a failed authority fsync can have advanced it.
                self._anchor.advance(hashlib.sha256(data).hexdigest(), temporary, restore,
                                     writer=self._security[0].write_identity, authorize_writer=authorize_writer)
            os.replace(temporary, DATABASE, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                if not admitted:
                    os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass

    def _operate(self, operation, key, value=None, expected=None):
        self._check_context()
        if self._pid != os.getpid():
            # A parent thread may have owned the operation lock at fork. Plain
            # cached values own no inherited SQLite connection or descriptor.
            self._operation_lock = threading.Lock()
            self._clear_cache()
            self._pid = os.getpid()
        deadline = time.monotonic() + LOCK_TIMEOUT
        while not self._operation_lock.acquire(timeout=min(.05, self._lock_remaining(deadline))):
            pass
        try:
            self._check_context()
            result = self._operate_locked(operation, key, value, expected, deadline)
            self._check_context()
            return result
        except ResourceLimitError:
            raise StorageError('database resource budget exceeded') from None
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
                raise StorageAuthorityError('database authority denied')
            descriptors = getattr(self._context, 'descriptors', None)
            self._security = storage_cipher(self.root, descriptors=descriptors)
            identity = None if self._security is None else self._security[2]
            if identity != self._security_identity:
                self._clear_cache()
                self._security_identity = identity
            # A single input must not allocate an unbounded SQLite snapshot.
            inputs = (key, value, expected) if operation not in ('get_many', 'compare_exchange_many', 'restore_anchor', 'fence_key') else ()
            if any(len(s) > MAX_BYTES // 2 or len(s.encode('utf-8')) > MAX_BYTES // 2
                   for s in inputs if s is not None):
                raise StorageError('database input size limit exceeded')
            if operation == 'migrate' and os.environ.get('GOPYT_STORE_ANCHOR_DIR'):
                raise StorageError('migration requires unanchored storage')
            with self._locked(deadline, enroll=operation == 'enroll_anchor',
                              restore=operation in ('restore_anchor', 'anchor_status')) as directory:
                if self._anchor is not None and operation not in ('get', 'get_many', 'anchor_status', 'enroll_anchor', 'fence_key'):
                    self._anchor.check_writer(self._security[0].write_identity)
                if operation == 'migrate':
                    from gopyt.migration import migrate
                    self._clear_cache()
                    return migrate(self, directory, value, expected, MAX_BYTES, deadline)
                if operation == 'fence_key':
                    if self._anchor is None or self._anchor.record['generation'] != expected:
                        raise StorageGenerationError('key authorization generation mismatch')
                if operation == 'restore_anchor':
                    self._clear_cache()
                    backup, generation, reason = value
                    if self._anchor is None or self._anchor.record['generation'] != generation:
                        raise StorageGenerationError('restoration generation mismatch')
                    db = sqlite3.connect(':memory:')
                    try:
                        db.execute('PRAGMA trusted_schema=OFF')
                        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_BYTES)
                        self._deserialize_snapshot(backup, db)
                        if db.execute("SELECT 1 FROM kv WHERE typeof(key) != 'text' OR typeof(value) != 'text' LIMIT 1").fetchone():
                            raise StorageError('invalid restoration values')
                        self._anchor._receipt()  # Preserve any prior admitted restoration evidence.
                        receipt = dict(generation=generation + 1, previous_generation=generation,
                                       backup_digest=hashlib.sha256(backup).hexdigest(), reason=reason)
                        self._save(directory, db, restore=receipt)
                        return dict(self._anchor.record)
                    finally:
                        db.close()
                if operation == 'anchor_status':
                    if self._anchor is None:
                        raise StorageError('anchor directory required')
                    return dict(self._anchor.record)
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
                        self._check_context()
                        page_size = db.execute('PRAGMA page_size').fetchone()[0]
                        db.execute(f'PRAGMA max_page_count={MAX_BYTES // page_size}')
                        if operation == 'enroll_anchor':
                            self._anchor.enroll(snapshot_digest(directory, DATABASE, MAX_BYTES + OVERHEAD, descriptors=descriptors),
                                                self._security[0].write_identity)
                            return True
                        if operation == 'fence_key':
                            self._save(directory, db, authorize_writer=True)
                            return dict(self._anchor.record)
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

    def restore_anchor(self, backup, *, expected_generation, reason):
        """Trusted operator restore of authenticated bytes as a new generation."""
        if type(backup) is not bytes:
            raise StorageError('invalid restoration request')
        return self._restore_anchor_bytes(backup, expected_generation, reason)

    def restore_anchor_file(self, path, *, expected_generation, reason):
        """Read and restore a backup using the caller's shared resource budget."""
        budget = getattr(self._context, 'resource_budget', None)
        if budget is None:
            raise StorageError('file restoration requires a resource budget')
        path = os.path.abspath(path)
        backup = None
        try:
            with regular_file('/', path.lstrip('/'), check_context=self._check_context,
                              descriptors=getattr(self._context, 'descriptors', None)) as stream:
                with read_payload(stream, budget, MAX_BYTES + OVERHEAD + 1,
                                  check=self._check_context) as payload:
                    backup = payload.data
                    return self._restore_anchor_bytes(backup, expected_generation, reason)
        finally:
            backup = None

    def _restore_anchor_bytes(self, backup, expected_generation, reason):
        try:
            if (not 0 < len(backup) <= MAX_BYTES + OVERHEAD
                    or type(expected_generation) is not int or not 0 <= expected_generation < (1 << 63) - 1
                    or not isinstance(reason, str) or not reason.strip()):
                raise StorageError('invalid restoration request')
            try:
                if len(reason.encode('utf-8')) > 512:
                    raise StorageError('restoration reason limit')
            except UnicodeError as error:
                raise StorageError('invalid restoration reason') from error
            return self._operate('restore_anchor', '*', (backup, expected_generation, reason))
        finally:
            backup = None

    def anchor_status(self):
        return self._operate('anchor_status', '*')

    def enroll_anchor(self):
        """Trusted operator enrollment; never called by language natives."""
        return self._operate('enroll_anchor', '*')

    def migrate(self, *, backup, expected_digest):
        """Trusted initial migration, with an encrypted recovery copy."""
        if (not isinstance(expected_digest, str) or len(expected_digest) != 64
                or any(c not in '0123456789abcdef' for c in expected_digest)):
            raise StorageError('invalid migration digest')
        return self._operate('migrate', '*', os.fspath(backup), expected_digest)

    def fence_key(self, *, expected_generation):
        """Trusted operator key transition; ordinary writes cannot grant key authority."""
        if type(expected_generation) is not int or not 0 <= expected_generation < (1 << 63) - 1:
            raise StorageError('invalid key authorization generation')
        return self._operate('fence_key', '*', expected=expected_generation)

    def rekey(self):
        """Trusted maintenance only; authenticate then republish using active key."""
        return self._operate('rekey', '*')
