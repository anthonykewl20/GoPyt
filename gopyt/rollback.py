"""Trusted host snapshot anchor; its directory must be outside rollback scope."""
from contextlib import contextmanager, ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import time

from gopyt.files import parent_directory, opened_descriptor
from gopyt.security_config import SecurityError

MAX_RECORD = 4096
MAX_GENERATION = (1 << 63) - 1


def _private(info, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_mode & 0o077
            or info.st_uid not in (0, os.geteuid())
            or (not directory and info.st_nlink != 1)):
        raise SecurityError('anchor requires private operator-owned files')


def _read(directory, name, maximum, *, descriptors=None):
    with opened_descriptor(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                           dir_fd=directory, descriptors=descriptors) as fd:
        _private(os.fstat(fd))
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise SecurityError('anchor record limit')
        return data


def snapshot_digest(directory, name, maximum, *, descriptors=None):
    owners = ExitStack()
    try:
        fd = owners.enter_context(opened_descriptor(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory, descriptors=descriptors))
    except FileNotFoundError:
        return None
    with owners:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= maximum:
            raise SecurityError('invalid anchored snapshot')
        digest, count = hashlib.sha256(), 0
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            while chunk := stream.read(65536):
                count += len(chunk)
                if count > maximum:
                    raise SecurityError('anchored snapshot limit')
                digest.update(chunk)
        after = os.fstat(fd)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if count != info.st_size or identity(after) != identity(info):
            raise SecurityError('anchored snapshot changed')
        return digest.hexdigest()


def _decode(data, identity):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SecurityError('duplicate anchor field')
            result[key] = value
        return result
    try:
        record = json.loads(data, object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise SecurityError('invalid anchor JSON') from error
    fields = {'version', 'store', 'generation', 'digest', 'stage', 'restore'}
    if isinstance(record, dict) and record.get('version') == 2:
        fields.add('writer')
    if (not isinstance(record, dict) or set(record) != fields
            or type(record['version']) is not int or record['version'] not in (1, 2)
            or record['store'] != identity or type(record['generation']) is not int
            or not 0 <= record['generation'] <= MAX_GENERATION):
        raise SecurityError('invalid anchor record')
    if record['version'] == 2:
        writer = record['writer']
        if not isinstance(writer, str) or len(writer) != 64 or any(c not in '0123456789abcdef' for c in writer):
            raise SecurityError('invalid writer identity')
    digest, stage = record['digest'], record['stage']
    if digest is not None and (not isinstance(digest, str) or len(digest) != 64
                              or any(c not in '0123456789abcdef' for c in digest)):
        raise SecurityError('invalid anchor digest')
    if stage is not None and (not isinstance(stage, str) or len(stage) != 33
                             or not stage.startswith('.pending-')
                             or any(c not in '0123456789abcdef' for c in stage[9:])):
        raise SecurityError('invalid anchor staging name')
    if (record['generation'] != 0 and (digest is None or stage is None)) or (digest is None and stage is not None):
        raise SecurityError('invalid anchor generation')
    restore = record['restore']
    if restore is not None:
        if (not isinstance(restore, dict) or set(restore) != {'generation', 'previous_generation', 'backup_digest', 'reason'}
                or type(restore['generation']) is not int or not 1 <= restore['generation'] <= record['generation']
                or type(restore['previous_generation']) is not int
                or restore['previous_generation'] != restore['generation'] - 1
                or not isinstance(restore['backup_digest'], str) or len(restore['backup_digest']) != 64
                or any(c not in '0123456789abcdef' for c in restore['backup_digest'])
                or not isinstance(restore['reason'], str) or not restore['reason'].strip()
                or len(restore['reason'].encode('utf-8')) > 512):
            raise SecurityError('invalid restoration record')
    return record


class Anchor:
    def __init__(self, directory, identity, record, *, descriptors=None):
        self.directory, self.identity, self.record = directory, identity, record
        self.descriptors = descriptors

    def _write(self, record):
        data = json.dumps(record, sort_keys=True, separators=(',', ':')).encode()
        if len(data) > MAX_RECORD:
            raise SecurityError('anchor record limit')
        temporary = '.anchor-' + secrets.token_hex(12)
        owners = ExitStack()
        fd = owners.enter_context(opened_descriptor(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=self.directory, descriptors=self.descriptors))
        try:
            with owners:
                with os.fdopen(fd, 'wb', closefd=False) as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            os.replace(temporary, 'record.json', src_dir_fd=self.directory, dst_dir_fd=self.directory)
            os.fsync(self.directory)
            self.record = record
            self._receipt()
        finally:
            try:
                os.unlink(temporary, dir_fd=self.directory)
            except FileNotFoundError:
                pass

    def enroll(self, digest, writer):
        if self.record is not None:
            raise SecurityError('anchor already enrolled')
        self._write(dict(version=2, store=self.identity, generation=0, digest=digest,
                         stage=None, restore=None, writer=writer))

    def check_writer(self, writer):
        if self.record is not None and self.record['version'] == 2 and self.record['writer'] != writer:
            raise SecurityError('active storage key is not authorized to publish')

    def advance(self, digest, stage, restore=None, *, writer, authorize_writer=False):
        if self.record is None or self.record['generation'] == MAX_GENERATION:
            raise SecurityError('anchor cannot advance')
        if not authorize_writer:
            self.check_writer(writer)
        record = dict(version=2 if authorize_writer else self.record['version'],
                      store=self.identity, generation=self.record['generation'] + 1,
                      digest=digest, stage=stage, restore=restore or self.record['restore'])
        if record['version'] == 2:
            record['writer'] = writer if authorize_writer else self.record['writer']
        self._write(record)

    def _receipt(self):
        restore = self.record['restore']
        if restore is None:
            return
        receipt = dict(version=1, store=self.identity, **restore)
        data = json.dumps(receipt, sort_keys=True, separators=(',', ':')).encode()
        name = 'restore-' + str(restore['generation']) + '.json'
        try:
            previous = _read(self.directory, name, MAX_RECORD, descriptors=self.descriptors)
        except FileNotFoundError:
            temporary = '.receipt-' + secrets.token_hex(12)
            owners = ExitStack()
            fd = owners.enter_context(opened_descriptor(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600, dir_fd=self.directory, descriptors=self.descriptors))
            try:
                with owners:
                    with os.fdopen(fd, 'wb', closefd=False) as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                # Holding the authority lock excludes another cooperating writer.
                os.replace(temporary, name, src_dir_fd=self.directory, dst_dir_fd=self.directory)
                os.fsync(self.directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=self.directory)
                except FileNotFoundError:
                    pass
        else:
            if previous != data:
                raise SecurityError('restoration receipt mismatch')
            os.fsync(self.directory)

    def recover(self, directory, database, maximum):
        if self.record is None:
            return  # Explicit enrollment authenticates existing data in Store.
        os.fsync(self.directory)  # Complete a prior ambiguous authority barrier.
        self._receipt()
        actual = snapshot_digest(directory, database, maximum, descriptors=self.descriptors)
        if actual == self.record['digest']:
            return
        stage = self.record['stage']
        if stage is None or snapshot_digest(directory, stage, maximum, descriptors=self.descriptors) != self.record['digest']:
            raise SecurityError('snapshot rollback or missing recovery evidence')
        os.replace(stage, database, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
        if snapshot_digest(directory, database, maximum, descriptors=self.descriptors) != self.record['digest']:
            raise SecurityError('snapshot changed during anchor recovery')


@contextmanager
def locked_anchor(root, remaining, *, enroll=False, descriptors=None):
    path = os.environ.get('GOPYT_STORE_ANCHOR_DIR')
    if not path:
        if enroll:
            raise SecurityError('anchor directory required')
        yield None
        return
    identity = os.environ.get('GOPYT_STORE_ID', '')
    if not identity or len(identity.encode()) > 256 or not os.path.isabs(path):
        raise SecurityError('anchor requires absolute directory and stable store identity')
    if Path(os.path.abspath(path)).is_relative_to(Path(os.path.abspath(root))):
        raise SecurityError('anchor must be outside application package')
    with ExitStack() as owners:
        def acquire(name, flags, mode=0o777, *, dir_fd):
            return owners.enter_context(opened_descriptor(
                name, flags, mode, dir_fd=dir_fd, descriptors=descriptors))
        with parent_directory('/', path.lstrip('/'), descriptors=descriptors) as (parent, name):
            directory = acquire(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        _private(os.fstat(directory), directory=True)
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        if enroll:
            try:
                fd = acquire('lock', flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
            except FileExistsError:
                fd = acquire('lock', flags, dir_fd=directory)
        else:
            fd = acquire('lock', flags, dir_fd=directory)
        if enroll:
            os.fsync(directory)
        info = os.fstat(fd)
        _private(info)
        while True:
            remaining()
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(.002, remaining()))
        current = os.stat('lock', dir_fd=directory, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise SecurityError('anchor lock changed')
        remaining()
        try:
            record = _decode(_read(directory, 'record.json', MAX_RECORD, descriptors=descriptors), identity)
        except FileNotFoundError:
            if not enroll:
                raise SecurityError('anchor is not enrolled')
            record = None
        yield Anchor(directory, identity, record, descriptors=descriptors)
