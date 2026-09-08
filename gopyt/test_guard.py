"""Exercise actual checker, VM, isolated gate, HTTP and SQLite failure boundaries."""
import concurrent.futures
import http.client
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from gopyt.guard import digest, evaluate, snapshot
from examples.guarded_refunds.freeze import HERE, bundle
from examples.guarded_refunds.service import Ledger, Server


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.candidate = self.root/'candidate'
        shutil.copytree(HERE/'policy', self.candidate)
        self.bundle = bundle()
        self.pin = digest(self.bundle)

    def tearDown(self):
        self.temp.cleanup()

    def verdict(self):
        return evaluate(self.bundle, self.pin, self.candidate)

    def test_clean_and_legitimate_body_change(self):
        self.assertTrue(self.verdict()['accepted'])
        p = self.candidate/'impl/refund_policy.gopyt'
        p.write_text(p.read_text().replace('return refunded + amount', 'return amount + refunded'))
        result = self.verdict()
        self.assertTrue(result['accepted'], result)
        self.assertEqual(result['changed_files'], ['impl/refund_policy.gopyt'])

    def test_both_contract_copies_cannot_erase_rule(self):
        for role in ('spec','impl'):
            p = self.candidate/role/'refund_policy.gopyt'
            p.write_text(p.read_text().replace('    ensures result <= paid\n',''))
        self.assertIn('protected files changed', self.verdict()['error'])

    def test_wrong_body_fails_executable_contract(self):
        p = self.candidate/'impl/refund_policy.gopyt'
        p.write_text(p.read_text().replace('return refunded + amount', 'return refunded + amount + 1'))
        result = self.verdict()
        self.assertFalse(result['accepted'])
        self.assertGreater(len(result['failures']), 0)

    def test_candidate_cannot_replace_gate_or_tests(self):
        for rel in ['gopyt.py','sitecustomize.py','test/lie.gopyt','.github/workflows/pass.yml']:
            with self.subTest(rel=rel):
                p = self.candidate/rel
                p.parent.mkdir(parents=True,exist_ok=True)
                p.write_text('print("accepted")')
                self.assertIn('inventory changed', self.verdict()['error'])
                p.unlink()

    def test_symlink_directory_and_file_refused(self):
        for rel in ['link','impl/link.gopyt']:
            p = self.candidate/rel
            p.symlink_to(self.root)
            self.assertFalse(self.verdict()['accepted'])
            p.unlink()

    def test_bundle_and_engine_tampering(self):
        self.assertFalse(evaluate(self.bundle+b' ',self.pin,self.candidate)['accepted'])
        body=json.loads(self.bundle)
        body['engine_sha256']='0'*64
        altered=json.dumps(body).encode()
        self.assertIn('engine hash mismatch', evaluate(altered,digest(altered),self.candidate)['error'])

    def test_manifest_and_spec_deletion_refused(self):
        p=self.candidate/'gopyt.toml'
        p.write_text(p.read_text()+'\n[dependencies]\nevil="../evil"\n')
        self.assertFalse(self.verdict()['accepted'])
        (self.candidate/'spec/refund_policy.gopyt').unlink()
        self.assertIn('inventory changed',self.verdict()['error'])

    def test_timeout_fails_closed(self):
        self.assertFalse(evaluate(self.bundle,self.pin,self.candidate,timeout=0.001)['accepted'])

    def test_oversized_input_refused(self):
        (self.candidate/'impl/refund_policy.gopyt').write_bytes(b' '*2_000_001)
        self.assertIn('size exceeds', self.verdict()['error'])

    def test_lock_is_derived_not_an_authority(self):
        (self.candidate/'gopyt.lock').write_text('attacker-controlled garbage')
        self.assertTrue(self.verdict()['accepted'])


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.bundle=bundle()
        self.ledger=Ledger(self.root/'db.sqlite',HERE/'policy',self.bundle,digest(self.bundle))
        self.ledger.seed([{'id':'order1','customer':'customer1','paid':100}])
        self.start()

    def start(self):
        self.server=Server(self.ledger,{'test-secret-token-longer-than-32-characters':'customer1'})
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def tearDown(self):
        self.stop()
        self.ledger.close()
        self.temp.cleanup()

    def body(self, **changes):
        return {'order_id':'order1','customer_id':'customer1','amount':30,
                'expected_version':1,'idempotency_key':'key1',**changes}

    def request(self, body=None, raw=None):
        raw=json.dumps(body).encode() if raw is None else raw
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        try:
            conn.request('POST','/refunds',body=raw,headers={'Content-Type':'application/json','Authorization':'Bearer test-secret-token-longer-than-32-characters'})
            response=conn.getresponse()
            return response.status,json.loads(response.read())
        finally:
            conn.close()

    def state(self):
        conn=self.ledger.connect()
        try:
            return tuple(conn.execute('SELECT refunded,version FROM orders').fetchone()),conn.execute('SELECT count(*) FROM requests').fetchone()[0]
        finally:
            conn.close()

    def test_replay_conflict_and_restart(self):
        first=self.request(self.body())
        self.assertEqual(first[0],200)
        self.assertEqual(first,self.request(self.body()))
        self.assertEqual(self.request(self.body(amount=31))[0],409)
        self.stop()
        self.ledger.close()
        self.ledger=Ledger(self.root/'db.sqlite',HERE/'policy',self.bundle,digest(self.bundle))
        self.start()
        self.assertEqual(first,self.request(self.body()))
        self.assertEqual(self.state(),((30,2),1))

    def test_remaining_balance_stale_version_closed_customer(self):
        self.assertEqual(self.request(self.body(customer_id='other'))[0],403)
        self.assertEqual(self.request(self.body(expected_version=2))[0],409)
        self.assertEqual(self.request(self.body(amount=101))[0],409)
        self.assertEqual(self.request(self.body(amount=100))[0],200)
        self.assertEqual(self.request(self.body(amount=1,expected_version=2,idempotency_key='next'))[0],409)
        conn=self.ledger.connect()
        conn.execute('UPDATE orders SET closed=1,refunded=0')
        conn.close()
        self.assertEqual(self.request(self.body(expected_version=2,idempotency_key='closed'))[0],409)

    def test_concurrent_unique_requests_cannot_double_spend(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda i:self.request(self.body(amount=100,idempotency_key='key'+str(i))),range(8)))
        self.assertEqual(sum(status==200 for status,_ in results),1)
        self.assertEqual(self.state(),((100,2),1))

    def test_concurrent_identical_requests_replay_one_commit(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:self.request(self.body()),range(8)))
        self.assertTrue(all(r==results[0] and r[0]==200 for r in results))
        self.assertEqual(self.state(),((30,2),1))

    def test_db_failure_between_update_and_receipt_rolls_back(self):
        conn=self.ledger.connect()
        conn.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON requests BEGIN SELECT RAISE(ABORT,'injected'); END")
        conn.close()
        self.assertEqual(self.request(self.body())[0],503)
        self.assertEqual(self.state(),((0,1),0))

    def test_runtime_trap_and_cancel_roll_back(self):
        from gopyt.vm import Trap, Cancelled
        for error in (Trap(2), Cancelled()):
            with patch('examples.guarded_refunds.service.timed_call',side_effect=error):
                self.assertEqual(self.request(self.body())[0],503)
                self.assertEqual(self.state(),((0,1),0))

    def test_strict_input(self):
        for changes in [{'amount':True},{'amount':1.0},{'amount':0},{'amount':10**12+1},
                        {'expected_version':True},{'extra':'bad'},{'order_id':'../escape'}]:
            self.assertEqual(self.request(self.body(**changes))[0],400)
        for raw in [b'[]',b'{"amount":1,"amount":2}',b'{"amount":NaN}',b'\xff']:
            self.assertEqual(self.request(raw=raw)[0],400)
        self.assertEqual(self.request(raw=b' '*4097)[0],413)
        self.assertEqual(self.state(),((0,1),0))

    def test_bad_adapter_pin_prevents_start(self):
        changed=json.loads(self.bundle)
        changed['adapter_sha256']='0'*64
        raw=json.dumps(changed).encode()
        with self.assertRaisesRegex(ValueError,'adapter hash'):
            Ledger(self.root/'other.db',HERE/'policy',raw,digest(raw))

    def test_missing_wrong_and_cross_customer_credentials(self):
        for headers,expected in [({},401),({'Authorization':'Bearer wrong'},401),
                                  ({'Authorization':'Bearer test-secret-token-longer-than-32-characters'},403)]:
            conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
            try:
                conn.request('POST','/refunds',json.dumps(self.body(customer_id='other')),
                             {'Content-Type':'application/json',**headers})
                response=conn.getresponse()
                self.assertEqual(response.status,expected)
                response.read()
            finally:
                conn.close()
        self.assertEqual(self.state(),((0,1),0))

    def test_process_death_between_update_and_receipt_rolls_back(self):
        import subprocess
        import sys
        import time
        marker=self.root/'uncommitted.marker'
        bp=self.root/'bundle.json';bp.write_bytes(self.bundle)
        conn=self.ledger.connect()
        conn.execute('CREATE TRIGGER pause_receipt BEFORE INSERT ON requests BEGIN SELECT pause_commit(); END')
        conn.close()
        script='''
import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from examples.guarded_refunds.service import Ledger
from gopyt.guard import digest
original=Ledger.connect
def connect(self):
    conn=original(self)
    def pause():
        Path(sys.argv[5]).write_text('ledger updated, receipt not inserted')
        time.sleep(60)
        return 0
    conn.create_function('pause_commit',0,pause)
    return conn
Ledger.connect=connect
raw=Path(sys.argv[4]).read_bytes()
ledger=Ledger(sys.argv[2],sys.argv[3],raw,digest(raw))
ledger.apply({'order_id':'order1','customer_id':'customer1','amount':30,'expected_version':1,'idempotency_key':'key1'})
'''
        child=subprocess.Popen([sys.executable,'-I','-c',script,str(HERE.parents[1]),
                                str(self.root/'db.sqlite'),str(HERE/'policy'),str(bp),str(marker)],
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+15
            while not marker.exists() and child.poll() is None and time.monotonic()<deadline:
                time.sleep(0.02)
            self.assertTrue(marker.exists(),'child did not reach injected commit boundary')
        finally:
            child.kill()
            child.communicate(timeout=10)
        self.assertEqual(self.state(),((0,1),0))
        conn=self.ledger.connect();conn.execute('DROP TRIGGER pause_receipt');conn.close()
        self.assertEqual(self.request(self.body())[0],200)
        self.assertEqual(self.state(),((30,2),1))

    def test_two_independent_ledgers_share_database_safely(self):
        other=Ledger(self.root/'db.sqlite',HERE/'policy',self.bundle,digest(self.bundle))
        from examples.guarded_refunds.service import Rejected
        def send(pair):
            ledger,index=pair
            try:
                ledger.apply(self.body(amount=100,idempotency_key='independent'+str(index)))
                return 200
            except Rejected as exc:
                return exc.status
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(send,[(self.ledger,1),(other,2)]))
            self.assertEqual(sorted(results),[200,409])
            self.assertEqual(self.state(),((100,2),1))
        finally:
            other.close()

    def test_unseen_wrong_body_is_blocked_by_real_runtime_contract(self):
        # This input is deliberately absent from the after_refund gate vectors.
        # Passing a finite gate must not substitute for contracts on live calls.
        candidate=self.root/'rare-bug'
        shutil.copytree(HERE/'policy',candidate)
        p=candidate/'impl/refund_policy.gopyt'
        p.write_text(p.read_text().replace('    return refunded + amount\n',
            '    if amount == 42 {\n        return refunded + amount + 1\n    }\n    return refunded + amount\n'))
        receipt=evaluate(self.bundle,digest(self.bundle),candidate)
        self.assertTrue(receipt['accepted'],receipt)
        self.stop();self.ledger.close()
        self.ledger=Ledger(self.root/'db.sqlite',candidate,self.bundle,digest(self.bundle))
        self.start()
        self.assertEqual(self.request(self.body(amount=42))[0],503)
        self.assertEqual(self.state(),((0,1),0))
        self.assertEqual(self.request(self.body())[0],200)
