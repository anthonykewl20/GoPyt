"""Current-key authority rejects stale publishers while retaining backup readers."""
import fcntl
import hashlib
import json
import multiprocessing
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from gopyt import test_rollback as fixtures
from gopyt.storage import Store, StorageError, DIRECTORY, DATABASE


def waiting_writer(root, ready, release, result):
    original = fcntl.flock
    notified = False
    def flock(*args):
        nonlocal notified
        try: return original(*args)
        except BlockingIOError:
            if not notified:
                notified = True; ready.set()
                if not release.wait(15): raise AssertionError('writer release watchdog')
            raise
    try:
        with patch('gopyt.storage.fcntl.flock', flock), patch('gopyt.storage.LOCK_TIMEOUT', 30):
            Store(root).put('balance', '9')
        result.send('published')
    except BaseException as error:
        result.send(type(error).__name__)
    finally: result.close()


def crash_fence(root, boundary):
    original = os.replace
    def replace(source, destination, *args, **kwargs):
        if boundary == 'before_authority' and destination == 'record.json': os._exit(77)
        if boundary == 'before_snapshot' and destination == DATABASE: os._exit(77)
        result = original(source, destination, *args, **kwargs)
        if boundary == 'after_snapshot' and destination == DATABASE: os._exit(77)
        return result
    with patch('gopyt.storage.os.replace', replace):
        Store(root).fence_key(expected_generation=1)


class WriterFence(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RollbackAuthority(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.base, self.root, self.anchor = self.fixture.base, self.fixture.root, self.fixture.anchor
        self.old = (self.base/'key').read_bytes(); self.new = os.urandom(32)
        self.rings = {}
        for name, active, keys in [('old','a',{'a':self.old,'b':self.new}),
                                    ('new','b',{'a':self.old,'b':self.new}),
                                    ('retired','b',{'b':self.new}),
                                    ('renamed','current',{'current':self.new})]:
            path = self.base/(name+'.json')
            path.write_text(json.dumps({'version':1,'active':active,'keys':{k:v.hex() for k,v in keys.items()}}))
            path.chmod(0o600); self.rings[name] = str(path)
        env = patch.dict(os.environ, {'GOPYT_STORE_KEY_FILE':'','GOPYT_STORE_KEYRING_FILE':self.rings['old']})
        env.start(); self.addCleanup(env.stop)
        self.store = Store(self.root); self.store.enroll_anchor(); self.store.put('balance','10')
        self.snapshot = self.root/DIRECTORY/DATABASE
        self.backup = self.snapshot.read_bytes()

    def ring(self, name):
        return patch.dict(os.environ, {'GOPYT_STORE_KEYRING_FILE':self.rings[name]})

    def record(self):
        return json.loads((self.anchor/'record.json').read_text())

    def rotate(self):
        with self.ring('new'): return Store(self.root).fence_key(expected_generation=1)

    def test_stale_writes_reject_without_changing_snapshot_or_generation(self):
        result = self.rotate(); snapshot = self.snapshot.read_bytes()
        self.assertEqual(result['version'], 2); self.assertEqual(result['generation'], 2)
        self.assertEqual(self.store.get('balance'), '10')  # Retained new reader remains usable.
        actions = [lambda:self.store.put('balance','9'),
                   lambda:self.store.compare_exchange('balance','absent','9'),
                   lambda:self.store.compare_exchange_many([('balance','absent','9')]),
                   self.store.rekey,
                   lambda:self.store.restore_anchor(self.backup, expected_generation=2, reason='old active')]
        for action in actions:
            with self.assertRaises(StorageError): action()
            self.assertEqual(self.record(), result); self.assertEqual(self.snapshot.read_bytes(), snapshot)
        with self.ring('retired'):
            child = subprocess.run([sys.executable,'-c',
                'from gopyt.storage import Store; import sys; assert Store(sys.argv[1]).get("balance") == "10"',str(self.root)],
                capture_output=True,text=True,timeout=15)
            self.assertEqual(child.returncode,0,child.stderr)
            Store(self.root).put('balance','8')

    def test_writer_identity_uses_key_material_not_label_or_reader_set(self):
        result = self.rotate()
        expected = hashlib.sha256(b'GoPyt writer key\0'+self.new+b'GOPYT-SIV1\0rollback-test').hexdigest()
        self.assertEqual(result['writer'], expected)
        with self.ring('renamed'): Store(self.root).put('balance','9')
        self.assertEqual(self.record()['writer'], expected)

    def test_stale_operator_generation_and_invalid_generations_reject(self):
        self.rotate(); before = self.record(); snapshot = self.snapshot.read_bytes()
        for generation in (0,1,True,-1,1<<63):
            with self.assertRaises(StorageError): self.store.fence_key(expected_generation=generation)
        self.assertEqual(self.record(),before); self.assertEqual(self.snapshot.read_bytes(),snapshot)

    def test_version_one_authority_requires_explicit_upgrade(self):
        record = self.record(); record['version'] = 1; del record['writer']
        (self.anchor/'record.json').write_text(json.dumps(record))
        self.store.put('balance','11')
        self.assertEqual(self.record()['version'],1)
        with self.ring('new'): result = Store(self.root).fence_key(expected_generation=2)
        self.assertEqual((result['version'],result['generation']),(2,3))
        with self.assertRaises(StorageError): self.store.put('balance','9')
        with self.ring('retired'): self.assertEqual(Store(self.root).get('balance'),'11')

    def test_backup_reader_does_not_regain_publishing_authority(self):
        self.rotate()
        with self.ring('new'):
            result = Store(self.root).restore_anchor(self.backup,expected_generation=2,reason='old backup with current writer')
        self.assertEqual(result['generation'],3)
        with self.ring('retired'): self.assertEqual(Store(self.root).get('balance'),'10')
        with self.assertRaises(StorageError): self.store.put('balance','9')

    def test_already_waiting_process_cannot_publish_loaded_old_settings(self):
        context = multiprocessing.get_context('spawn')
        ready, release = context.Event(), context.Event(); reader, writer = context.Pipe(duplex=False)
        fd = os.open(self.root/DIRECTORY/'lock',os.O_RDWR); fcntl.flock(fd,fcntl.LOCK_EX)
        child = context.Process(target=waiting_writer,args=(str(self.root),ready,release,writer));child.start();writer.close()
        try:
            self.assertTrue(ready.wait(10))
            os.close(fd); fd = None
            result = self.rotate(); release.set()
            self.assertTrue(reader.poll(10)); self.assertEqual(reader.recv(),'StorageError')
            child.join(10); self.assertEqual(child.exitcode,0)
            self.assertEqual(self.record(),result)
            with self.ring('retired'): self.assertEqual(Store(self.root).get('balance'),'10')
        finally:
            if fd is not None: os.close(fd)
            release.set(); reader.close()
            if child.is_alive(): child.kill(); child.join()
            child.close()

    def test_crash_transition_recovers_exact_writer_policy(self):
        context = multiprocessing.get_context('spawn')
        old_record = self.record(); old_snapshot = self.snapshot.read_bytes()
        for boundary in ('before_authority','before_snapshot','after_snapshot'):
            with self.subTest(boundary=boundary):
                # Restore fixture state as trusted test setup, not a supported operator downgrade.
                (self.anchor/'record.json').write_text(json.dumps(old_record));self.snapshot.write_bytes(old_snapshot)
                with self.ring('new'):
                    child = context.Process(target=crash_fence,args=(str(self.root),boundary));child.start()
                    try:
                        child.join(10);self.assertEqual(child.exitcode,77)
                    finally:
                        if child.is_alive(): child.kill();child.join()
                        child.close()
                self.assertEqual(self.store.get('balance'),'10')
                if boundary == 'before_authority':
                    self.assertEqual(self.record(),old_record)
                else:
                    with self.assertRaises(StorageError):self.store.put('balance','9')
                    with self.ring('retired'):self.assertEqual(Store(self.root).get('balance'),'10')
                    self.assertEqual(self.record()['generation'],2)

    def test_cli_and_malformed_writer_metadata(self):
        with self.ring('new'):
            result = subprocess.run([sys.executable,'-m','gopyt.store_admin','fence-key','--root',str(self.root),
                                     '--expected-generation','1'],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['generation'],2)
        record = self.record();record['writer'] = 'not-a-key-identity'
        (self.anchor/'record.json').write_text(json.dumps(record))
        with self.assertRaises(StorageError): self.store.get('balance')


if __name__ == '__main__': unittest.main()
