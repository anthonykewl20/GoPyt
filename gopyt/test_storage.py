"""Persistent native storage: process boundaries, atomic races and confinement."""
from contextlib import closing
import concurrent.futures
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from gopyt.cli import build
from gopyt.storage import Store, StorageError, DIRECTORY, DATABASE
from gopyt.testing import write_pkg
from gopyt.test_vm import module
from gopyt.values import Record
from gopyt.vm import VM, Trap


class Storage(unittest.TestCase):
    def test_directory_lock_and_snapshot_share_descriptor_budget(self):
        from gopyt.resource_budget import ResourceBudget, ResourceLimits
        from gopyt.resource_descriptors import DescriptorRegistry
        class Context:
            deadline_ns = None
            def __init__(self, registry): self.descriptors = registry
            def check_cancelled(self): pass
        self.store.put('key', 'value')
        before = Path(self.root, DIRECTORY, DATABASE).read_bytes()
        for capacity in (0, 1, 2, 3):
            with self.subTest(capacity=capacity):
                budget = ResourceBudget(ResourceLimits(0, 0, capacity, 0))
                registry = DescriptorRegistry(budget)
                context = Context(registry)
                store = Store(self.root, context=context)
                if capacity < 3:
                    with self.assertRaisesRegex(StorageError, 'resource budget'):
                        store.get('key')
                else:
                    self.assertEqual(store.get('key'), 'value')
                    self.assertEqual(budget.snapshot()['peak']['descriptors'], 3)
                self.assertEqual(Path(self.root, DIRECTORY, DATABASE).read_bytes(), before)
                self.assertEqual(registry.pending(), 0)
                self.assertEqual(budget.snapshot()['active_reservations'], 0)
                self.assertTrue(registry.close())

    def test_snapshot_descriptor_scope_rejects_and_unwinds_without_hiding_body_errors(self):
        from gopyt.resource_budget import ResourceBudget, ResourceLimits, ResourceLimitError
        from gopyt.resource_descriptors import DescriptorRegistry
        class Context:
            deadline_ns = None
            def __init__(self, registry): self.descriptors = registry
            def check_cancelled(self): pass
        self.store.put('key', 'value')
        directory = os.open(Path(self.root, DIRECTORY), os.O_RDONLY | os.O_DIRECTORY)
        try:
            for capacity in (0, 1):
                budget = ResourceBudget(ResourceLimits(0, 0, capacity, 0))
                registry = DescriptorRegistry(budget)
                context = Context(registry)
                store = Store(self.root, context=context)
                if capacity == 0:
                    with self.assertRaises(ResourceLimitError):
                        with store._snapshot(directory):
                            self.fail('snapshot opened without capacity')
                else:
                    with self.assertRaisesRegex(FileNotFoundError, 'body failed'):
                        with store._snapshot(directory) as (fd, identity):
                            self.assertEqual(budget.snapshot()['used']['descriptors'], 1)
                            self.assertIsNotNone(identity)
                            raise FileNotFoundError('body failed')
                    with self.assertRaises(OSError): os.fstat(fd)
                self.assertEqual(registry.pending(), 0)
                self.assertEqual(budget.snapshot()['active_reservations'], 0)
                self.assertTrue(registry.close())
        finally:
            os.close(directory)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = self.temporary.name
        self.store = Store(self.root)

    def child(self, source):
        return subprocess.run([sys.executable, '-c', source, self.root],
                              capture_output=True, text=True, timeout=20)

    def test_acknowledged_write_survives_abrupt_process_exit(self):
        result = self.child('import os,sys; from gopyt.storage import Store; Store(sys.argv[1]).put("ticket", "v1"); os._exit(0)')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.store.get('ticket'), 'v1')

    def test_absent_is_distinct_from_empty_and_unicode_values(self):
        self.assertIsNone(self.store.get('missing'))
        self.assertTrue(self.store.compare_exchange('ticket', None, ''))
        self.assertFalse(self.store.compare_exchange('ticket', None, 'wrong'))
        self.assertTrue(self.store.compare_exchange('ticket', '', 'á\0雪'))
        self.assertEqual(Store(self.root).get('ticket'), 'á\0雪')
        self.assertFalse(self.store.compare_exchange('ticket', 'wrong', 'changed'))
        self.assertEqual(self.store.get('ticket'), 'á\0雪')

    def test_lock_creation_retries_a_transient_missing_name(self):
        original = os.open
        injected = []
        def opening(path, flags, *args, **kwargs):
            if path == 'lock' and flags & os.O_CREAT and not injected:
                injected.append(True)
                raise FileNotFoundError('simulated concurrent creation race')
            return original(path, flags, *args, **kwargs)
        with patch('gopyt.storage.os.open', side_effect=opening):
            self.store.put('key', 'retained')
        self.assertEqual(injected, [True])
        self.assertEqual(Store(self.root).get('key'), 'retained')

    def test_one_winner_for_threaded_conditional_insert(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            wins = list(pool.map(lambda i: Store(self.root).compare_exchange('ticket', None, str(i)), range(32)))
        self.assertEqual(sum(wins), 1)

    def test_one_winner_for_process_stale_updates(self):
        self.store.put('ticket', 'v1')
        source = 'import sys; from gopyt.storage import Store; print(Store(sys.argv[1]).compare_exchange("ticket", "v1", str(__import__("os").getpid())))'
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(lambda _: self.child(source), range(8)))
        self.assertTrue(all(p.returncode == 0 for p in outcomes), [p.stderr for p in outcomes])
        self.assertEqual(sum(p.stdout.strip() == 'True' for p in outcomes), 1)

    def test_process_cas_increment_has_no_lost_updates(self):
        self.store.put('counter', '0')
        source = '''import sys
from gopyt.storage import Store
db=Store(sys.argv[1])
for _ in range(30):
    while True:
        old=db.get('counter')
        if db.compare_exchange('counter',old,str(int(old)+1)): break
'''
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(lambda _: self.child(source), range(4)))
        self.assertTrue(all(p.returncode == 0 for p in outcomes), [p.stderr for p in outcomes])
        self.assertEqual(self.store.get('counter'), '120')

    def test_separate_packages_and_implicit_stores_are_isolated(self):
        self.store.put('ticket', 'v1')
        first, second = Store(), Store()
        first.put('ticket', 'private')
        self.assertIsNone(second.get('ticket'))
        self.assertEqual(self.store.get('ticket'), 'v1')
        with tempfile.TemporaryDirectory() as other:
            self.assertIsNone(Store(other).get('ticket'))

    def test_directory_symlink_cannot_escape(self):
        with tempfile.TemporaryDirectory() as outside:
            Path(self.root, DIRECTORY).symlink_to(outside, target_is_directory=True)
            with self.assertRaises(StorageError):
                self.store.put('ticket', 'bad')
            self.assertEqual(list(Path(outside).iterdir()), [])

    def test_database_and_lock_links_are_rejected(self):
        for name in (DATABASE, 'lock'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as root:
                state = Path(root, DIRECTORY)
                state.mkdir()
                sentinel = Path(root, 'outside')
                sentinel.write_bytes(b'untouched')
                (state / name).symlink_to(sentinel)
                with self.assertRaises(StorageError):
                    Store(root).put('ticket', 'bad')
                self.assertEqual(sentinel.read_bytes(), b'untouched')

    def test_database_hardlink_fifo_and_corruption_are_rejected(self):
        self.store.put('ticket', 'v1')
        database = Path(self.root, DIRECTORY, DATABASE)
        os.link(database, Path(self.root, 'alias'))
        with self.assertRaises(StorageError):
            self.store.get('ticket')
        database.unlink()
        os.mkfifo(database)
        with self.assertRaises(StorageError):
            self.store.get('ticket')
        database.unlink()
        database.write_bytes(b'not sqlite')
        with self.assertRaises(StorageError):
            self.store.get('ticket')

    def test_lock_wait_is_bounded(self):
        import fcntl
        self.store.put('ticket', 'v1')
        with open(Path(self.root, DIRECTORY, 'lock'), 'rb') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            with patch('gopyt.storage.LOCK_TIMEOUT', 0.01):
                with self.assertRaisesRegex(StorageError, 'busy'):
                    self.store.put('ticket', 'v2')
        self.assertEqual(self.store.get('ticket'), 'v1')

    def test_failed_commit_preserves_prior_database(self):
        self.store.put('ticket', 'v1')
        with patch('gopyt.storage.os.replace', side_effect=OSError('injected disk failure')):
            with self.assertRaises(StorageError):
                self.store.put('ticket', 'v2')
        self.assertEqual(Store(self.root).get('ticket'), 'v1')
        self.assertEqual(sorted(p.name for p in Path(self.root, DIRECTORY).iterdir()), ['lock', DATABASE])

    def test_crash_before_commit_preserves_database_and_cleans_temporary(self):
        self.store.put('ticket', 'v1')
        result = self.child('import os,sys; import gopyt.storage as s; s.os.replace=lambda *a,**kw:os._exit(7); s.Store(sys.argv[1]).put("ticket","v2")')
        self.assertEqual(result.returncode, 7)
        self.assertTrue(list(Path(self.root, DIRECTORY).glob('.pending-*')))
        self.assertEqual(self.store.get('ticket'), 'v1')
        self.assertFalse(list(Path(self.root, DIRECTORY).glob('.pending-*')))

    def test_size_error_does_not_replace_existing_value(self):
        self.store.put('ticket', 'v1')
        with patch('gopyt.storage.MAX_BYTES', 16384):
            with self.assertRaises(StorageError):
                self.store.put('ticket', 'x' * 8193)
            for i in range(10):
                try:
                    self.store.put(str(i), 'x' * 4000)
                except StorageError:
                    break
            else:
                self.fail('database size cap did not reject growth')
        self.assertEqual(self.store.get('ticket'), 'v1')

    def test_warm_reads_and_absence_reuse_only_validated_results(self):
        self.store.put('ticket', 'v1')
        with patch.object(self.store, '_load', wraps=self.store._load) as load:
            self.assertEqual(self.store.get('ticket'), 'v1')
            self.assertIsNone(self.store.get('absent'))
            for _ in range(4):
                self.assertEqual(self.store.get('ticket'), 'v1')
                self.assertIsNone(self.store.get('absent'))
            self.assertEqual(load.call_count, 2)
        result = self.child('import sys; from gopyt.storage import Store; s=Store(sys.argv[1]); s.put("ticket","v2"); s.put("absent","present")')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.store.get('ticket'), 'v2')
        self.assertEqual(self.store.get('absent'), 'present')

    def test_removed_and_recreated_database_invalidates_cached_value(self):
        self.store.put('ticket', 'v1')
        self.assertEqual(self.store.get('ticket'), 'v1')
        Path(self.root, DIRECTORY, DATABASE).unlink()
        self.assertIsNone(self.store.get('ticket'))
        Store(self.root).put('ticket', 'new')
        self.assertEqual(self.store.get('ticket'), 'new')

    def test_warm_cache_rejects_unsafe_replacement_and_corruption(self):
        for kind in ('symlink', 'hardlink', 'fifo', 'corrupt', 'empty', 'oversize'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as root:
                store = Store(root)
                store.put('ticket', 'v1')
                self.assertEqual(store.get('ticket'), 'v1')
                database = Path(root, DIRECTORY, DATABASE)
                sentinel = Path(root, 'outside')
                sentinel.write_bytes(b'untouched')
                if kind == 'hardlink':
                    os.link(database, Path(root, 'alias'))
                else:
                    database.unlink()
                    if kind == 'symlink':
                        database.symlink_to(sentinel)
                    elif kind == 'fifo':
                        os.mkfifo(database)
                    elif kind == 'oversize':
                        with database.open('wb') as stream:
                            stream.truncate(64 * 1024 * 1024 + 1)
                    else:
                        database.write_bytes(b'not sqlite' if kind == 'corrupt' else b'')
                with self.assertRaises(StorageError):
                    store.get('ticket')
                self.assertEqual(sentinel.read_bytes(), b'untouched')
                self.assertFalse(store._cache)

    def test_in_place_change_with_restored_mtime_invalidates_cache(self):
        import sqlite3
        self.store.put('ticket', 'v1')
        self.assertEqual(self.store.get('ticket'), 'v1')
        database = Path(self.root, DIRECTORY, DATABASE)
        before = database.stat()
        with closing(sqlite3.connect(':memory:')) as db:
            db.deserialize(database.read_bytes())
            db.execute("UPDATE kv SET value='v2' WHERE key='ticket'")
            db.commit()
            data = db.serialize()
        database.write_bytes(data)
        os.utime(database, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(database.stat().st_ino, before.st_ino)
        self.assertEqual(database.stat().st_size, before.st_size)
        self.assertEqual(self.store.get('ticket'), 'v2')

    def test_warm_cache_revalidates_schema_and_value_types(self):
        import sqlite3
        for sql in ("CREATE TABLE extra (value TEXT)", "UPDATE kv SET value=x'0001'"):
            with self.subTest(sql=sql):
                self.store.put('ticket', 'v1')
                self.assertEqual(self.store.get('ticket'), 'v1')
                database = Path(self.root, DIRECTORY, DATABASE)
                with closing(sqlite3.connect(':memory:')) as db:
                    db.deserialize(database.read_bytes())
                    db.execute(sql)
                    db.commit()
                    data = db.serialize()
                database.write_bytes(data)
                with self.assertRaises(StorageError):
                    self.store.get('ticket')
                database.unlink()

    def test_cache_lru_bounds_utf8_payload_and_entry_count(self):
        for key, value in (('a', '雪'), ('b', '雪'), ('c', '雪'), ('huge', '雪' * 8)):
            self.store.put(key, value)
        with patch('gopyt.storage.CACHE_BYTES', 8), patch('gopyt.storage.CACHE_ENTRIES', 2):
            for key in ('a', 'b', 'a', 'c'):
                self.assertEqual(self.store.get(key), '雪')
            self.assertEqual(list(self.store._cache), ['a', 'c'])
            self.assertEqual(self.store._cache_bytes, 8)
            self.assertEqual(self.store.get('huge'), '雪' * 8)
            self.assertNotIn('huge', self.store._cache)
            self.assertIsNone(self.store.get('missing'))
            self.assertLessEqual(self.store._cache_bytes, 8)
            self.assertLessEqual(len(self.store._cache), 2)

    def test_failed_write_never_exposes_unpersisted_cached_value(self):
        self.store.put('ticket', 'v1')
        self.assertEqual(self.store.get('ticket'), 'v1')
        with patch('gopyt.storage.os.replace', side_effect=OSError('injected disk failure')):
            with self.assertRaises(StorageError):
                self.store.put('ticket', 'v2')
        self.assertFalse(self.store._cache)
        self.assertEqual(self.store.get('ticket'), 'v1')

    def test_post_rename_failure_reads_back_actual_persisted_state(self):
        import stat
        self.store.put('ticket', 'v1')
        self.assertEqual(self.store.get('ticket'), 'v1')
        fsync = os.fsync
        def fail_directory_sync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('injected directory sync failure')
            return fsync(fd)
        with patch('gopyt.storage.os.fsync', side_effect=fail_directory_sync):
            with self.assertRaises(StorageError):
                self.store.compare_exchange('ticket', 'v1', 'v2')
        self.assertFalse(self.store._cache)
        self.assertEqual(self.store.get('ticket'), 'v2')
        self.assertEqual(Store(self.root).get('ticket'), 'v2')

    def test_same_store_threads_share_atomic_cas_and_bounded_lock_wait(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            wins = list(pool.map(lambda i: self.store.compare_exchange('ticket', None, str(i)), range(32)))
        self.assertEqual(sum(wins), 1)
        with self.store._operation_lock, patch('gopyt.storage.LOCK_TIMEOUT', 0.01):
            with self.assertRaisesRegex(StorageError, 'busy'):
                self.store.get('ticket')

    def test_fork_discards_inherited_cache_and_locked_python_mutex(self):
        result = self.child('''import os,sys
from gopyt.storage import Store
store=Store(sys.argv[1])
store.put('ticket','v1')
assert store.get('ticket') == 'v1'
Store(sys.argv[1]).put('ticket','v2')
store._operation_lock.acquire()
pid=os.fork()
if pid == 0:
    try:
        assert store.get('ticket') == 'v2'
        store.put('ticket','child')
    except BaseException:
        os._exit(3)
    os._exit(0)
store._operation_lock.release()
_, status=os.waitpid(pid,0)
assert os.waitstatus_to_exitcode(status) == 0, status
assert store.get('ticket') == 'child'
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_compiles_without_app_ffi_and_persists_between_vms(self):
        files = module(
            'task create() -> bool | DbError\n    effects { database.read, database.write }\n\ntask read() -> str | NotFound | DbError\n    effects { database.read }\n\ntask invalid() -> bool | DbError\n    effects { database.read, database.write }\n',
            'task create() -> bool | DbError\n    effects { database.read, database.write }\n{\n    return store.db.compare_exchange("ticket", none, "v1")\n}\n\ntask read() -> str | NotFound | DbError\n    effects { database.read }\n{\n    return store.db.get("ticket")\n}\n\ntask invalid() -> bool | DbError\n    effects { database.read, database.write }\n{\n    return store.db.compare_exchange("", none, "v1")\n}\n',
            uses='use core.status { DbError, NotFound }\nuse store.db { compare_exchange, get }',
            spec_uses='use core.status { DbError, NotFound }')
        write_pkg(self.root, files, fmt=True)
        _, art, ids = build(self.root)
        self.assertIs(VM(art, self.root).call(ids['demo.create'], []), True)
        self.assertIs(VM(art, self.root).call(ids['demo.create'], []), False)
        self.assertEqual(VM(art, self.root).call(ids['demo.read'], []), 'v1')
        with self.assertRaises(Trap):
            VM(art, self.root).call(ids['demo.invalid'], [])
        Path(self.root, DIRECTORY, DATABASE).write_bytes(b'corrupt')
        vm = VM(art, self.root)
        error = vm.call(ids['demo.read'], [])
        self.assertIsInstance(error, Record)
        self.assertEqual(vm.type_name(error.type_id), 'core.status.DbError')


if __name__ == '__main__':
    unittest.main()
