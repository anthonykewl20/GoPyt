"""Atomic multi-key storage through the host and compiled language boundary."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import random
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
from gopyt.vm import VM


class Transactions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = os.path.realpath(self.temp.name)
        self.db = Store(self.root)

    def test_conflict_changes_nothing_and_delete_distinguishes_absent_empty(self):
        self.assertTrue(self.db.compare_exchange_many([('a', None, ''), ('b', None, '雪')]))
        self.assertFalse(self.db.compare_exchange_many([('a', '', 'changed'), ('b', 'stale', None)]))
        self.assertEqual(self.db.get_many(['b', 'a', 'missing']), ['雪', '', None])
        self.assertTrue(self.db.compare_exchange_many([('a', '', None), ('b', '雪', '')]))
        self.assertEqual(Store(self.root).get_many(['a', 'b']), [None, ''])

    def test_read_only_conditions_and_noop_do_not_publish(self):
        self.db.put('a', '1')
        path = Path(self.root, DIRECTORY, DATABASE)
        before = path.stat()
        with patch.object(self.db, '_save', side_effect=AssertionError('unexpected write')):
            self.assertTrue(self.db.compare_exchange_many([('a', '1', '1'), ('absent', None, None)]))
            self.assertFalse(self.db.compare_exchange_many([('a', 'wrong', '2')]))
        self.assertEqual(path.stat().st_ino, before.st_ino)

    def test_rejects_duplicate_malformed_and_unbounded_batches(self):
        for rows in ([], [('x', None, 'a')]*257, [('x', None, 'a'), ('x', 'a', 'b')],
                     [('', None, 'a')], [('x', 4, 'a')], [('x', None)],
                     [('x', None, '\ud800')], iter([('x', None, 'a')])):
            with self.subTest(rows=type(rows).__name__), self.assertRaises(StorageError):
                self.db.compare_exchange_many(rows)
        for keys in ([], ['x', 'x'], [''], [None], ['x']*257):
            with self.assertRaises(StorageError): self.db.get_many(keys)
        with patch('gopyt.storage.MAX_BATCH_BYTES', 5):
            with self.assertRaises(StorageError):
                self.db.compare_exchange_many([('x', None, '雪雪')])
        self.assertIsNone(self.db.get('x'))

    def test_oversized_batch_response_is_an_error(self):
        self.db.put('a', '1234'); self.db.put('b', '5678')
        with patch('gopyt.storage.MAX_BATCH_BYTES', 5):
            with self.assertRaises(StorageError): self.db.get_many(['a', 'b'])

    def test_batch_cache_reuse_does_not_double_count_or_return_stale_values(self):
        self.db.put('a', '1'); self.db.put('b', '2')
        self.assertEqual(self.db.get('a'), '1')
        for _ in range(20):
            self.assertEqual(self.db.get_many(['a','b']), ['1','2'])
        self.assertEqual(self.db._cache_bytes, 4)
        with patch.object(self.db,'_load',side_effect=AssertionError('unexpected cache reload')):
            self.assertEqual(self.db.get_many(['a','b']), ['1','2'])
        Store(self.root).compare_exchange_many([('a','1','3'),('b','2','4')])
        self.assertEqual(self.db.get_many(['a','b']), ['3','4'])

    def test_directory_sync_failure_has_ambiguous_commit_but_no_torn_batch(self):
        import stat
        self.db.compare_exchange_many([('a', None, '0'), ('b', None, '0')])
        self.assertEqual(self.db.get_many(['a','b']), ['0','0'])
        original=os.fsync
        def syncing(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):raise OSError('injected directory sync failure')
            return original(fd)
        with patch('gopyt.storage.os.fsync',side_effect=syncing):
            with self.assertRaises(StorageError):
                self.db.compare_exchange_many([('a','0','1'),('b','0','1')])
        self.assertEqual(Store(self.root).get_many(['a','b']), ['1','1'])
        self.assertEqual(self.db.get_many(['a','b']), ['1','1'])

    def test_seeded_state_machine_against_independent_dictionary(self):
        rng, oracle = random.Random(907), {}
        for step in range(1000):
            keys = rng.sample([str(i) for i in range(20)], rng.randint(1, 8))
            changes = [(key, oracle.get(key) if rng.randrange(4) else 'stale',
                        None if rng.randrange(3) == 0 else str(rng.randrange(100))) for key in keys]
            accepted = all(oracle.get(key) == expected for key, expected, _ in changes)
            self.assertEqual(self.db.compare_exchange_many(changes), accepted, step)
            if accepted:
                for key, _, value in changes:
                    if value is None: oracle.pop(key, None)
                    else: oracle[key] = value
            self.assertEqual(self.db.get_many(keys), [oracle.get(key) for key in keys], step)
        self.assertEqual(Store(self.root).get_many([str(i) for i in range(20)]),
                         [oracle.get(str(i)) for i in range(20)])

    def test_competing_processes_preserve_transfer_invariant(self):
        self.db.compare_exchange_many([('a', None, '1000'), ('b', None, '0')])
        source = '''import sys
from gopyt.storage import Store
db=Store(sys.argv[1])
for _ in range(60):
    for attempt in range(1000):
        a,b=db.get_many(['a','b'])
        assert int(a)+int(b)==1000
        if db.compare_exchange_many([('a',a,str(int(a)-1)),('b',b,str(int(b)+1))]): break
    else: raise AssertionError('retry budget exhausted')
'''
        def child(_):
            return subprocess.run([sys.executable, '-c', source, self.root], capture_output=True,
                                  text=True, timeout=60)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(child, range(4)))
        self.assertTrue(all(r.returncode == 0 for r in results), [r.stderr for r in results])
        self.assertEqual(Store(self.root).get_many(['a', 'b']), ['760', '240'])

    def test_failure_before_publish_preserves_all_old_values(self):
        self.db.compare_exchange_many([('a', None, '0'), ('b', None, '0')])
        with patch('gopyt.storage.os.replace', side_effect=OSError('injected')):
            with self.assertRaises(StorageError):
                self.db.compare_exchange_many([('a', '0', '1'), ('b', '0', '1')])
        self.assertEqual(Store(self.root).get_many(['a', 'b']), ['0', '0'])

    def test_process_death_before_and_after_publish_never_tears_batch(self):
        for after in (False, True):
            with self.subTest(after=after):
                self.db.put('a', '0'); self.db.put('b', '0')
                source = '''import os,sys
from unittest.mock import patch
from gopyt.storage import Store
original=os.replace
def die(*args,**kwargs):
    if sys.argv[2]=='True': original(*args,**kwargs)
    os._exit(73)
with patch('gopyt.storage.os.replace',side_effect=die):
    Store(sys.argv[1]).compare_exchange_many([('a','0','1'),('b','0','1')])
'''
                result = subprocess.run([sys.executable, '-c', source, self.root, str(after)], timeout=20)
                self.assertEqual(result.returncode, 73)
                self.assertEqual(Store(self.root).get_many(['a', 'b']), ['1','1'] if after else ['0','0'])
                self.assertFalse(list(Path(self.root, DIRECTORY).glob('.pending-*')))

    def test_compiled_native_types_effects_and_atomicity(self):
        signatures = '''task apply() -> bool | DbError
    effects { database.read, database.write }

task read() -> Snapshot | DbError
    effects { database.read }
'''
        bodies = '''task apply() -> bool | DbError
    effects { database.read, database.write }
{
    changes = core.list.empty[Change]()
    first = core.list.append[Change](changes, Change { key: "a"
        expected: none
        value: some("1") })
    both = core.list.append[Change](first, Change { key: "b"
        expected: none
        value: some("2") })
    return store.db.compare_exchange_many(both)
}

task read() -> Snapshot | DbError
    effects { database.read }
{
    keys = core.list.empty[str]()
    first = core.list.append[str](keys, "a")
    both = core.list.append[str](first, "b")
    return store.db.get_many(both)
}
'''
        files = module(signatures, bodies,
                       uses='use core.list { empty, append }\nuse core.status { DbError }\nuse store.db { Change, Snapshot, compare_exchange_many, get_many }',
                       spec_uses='use core.status { DbError }\nuse store.db { Snapshot }')
        write_pkg(self.root, files, fmt=True)
        _, art, ids = build(self.root)
        vm = VM(art, self.root)
        self.assertIs(vm.call(ids['demo.apply'], []), True)
        self.assertIs(vm.call(ids['demo.apply'], []), False)
        result = VM(art, self.root).call(ids['demo.read'], [])
        self.assertIsInstance(result, Record)
        self.assertEqual([v.value for v in result.fields[0]], ['1', '2'])


if __name__ == '__main__':
    unittest.main()
