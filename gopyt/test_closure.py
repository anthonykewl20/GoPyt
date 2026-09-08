"""Independent closure probes: heap roots, bounded state, crash recovery,
artifact authority and deterministic elaboration. No application effect mocks.
"""
import gc
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import weakref
from unittest.mock import patch

from gopyt import gobyte as G, ops as O
from gopyt.cli import build
from gopyt.check import load_package, check_package
from gopyt.diag import CompileError
from gopyt.heap import Heap
from gopyt.values import Record, Some
from gopyt.testing import write_pkg, run_cli
from gopyt.test_vm import module
from gopyt.vm import VM, Trap


class Memory(unittest.TestCase):
    def test_cycles_are_swept_with_host_cycle_gc_disabled(self):
        heap = Heap()
        cell = Record(0, [])
        cell.fields.append(cell)
        reference = weakref.ref(cell)
        heap.adopt(cell)
        enabled = gc.isenabled()
        gc.disable()
        try:
            del cell
            self.assertIsNotNone(reference())  # registry owns the cell
            self.assertEqual(heap.collect(), 1)
            self.assertIsNone(reference())    # collector severed the cycle
        finally:
            if enabled:
                gc.enable()

    def test_root_keeps_transitive_graph_and_releases_it(self):
        heap = Heap()
        cell = Record(0, [Some(['payload', {'key': b'bytes'}])])
        with heap.pin(cell):
            heap.collect()
            self.assertEqual(cell.fields[0].value[0], 'payload')
            self.assertGreaterEqual(len(heap.objects), 7)
        self.assertGreater(heap.collect(), 0)
        self.assertEqual(len(heap.objects), 0)

    def test_collection_during_parallel_calls_preserves_results(self):
        with tempfile.TemporaryDirectory() as root:
            spec = 'type Box {\n    text: str\n}\n\ntask make() -> Box\n    effects { time }\n\ntask main() -> list[Box]\n    effects { time }\n'
            impl = 'task make() -> Box\n    effects { time }\n{\n    core.time.sleep_ms(1)\n    return Box { text: "retained" }\n}\n\ntask main() -> list[Box]\n    effects { time }\n{\n    return parallel max 4 timeout_ms 10000 {\n        make()\n        make()\n        make()\n        make()\n    }\n}\n'
            write_pkg(root, module(spec, impl, uses='use core.time { sleep_ms }'), fmt=True)
            prog, art, ids = build(root)
            vm = VM(art, root)
            stop = threading.Event()
            errors = []
            def collect():
                try:
                    while not stop.wait(0.0001):
                        vm.heap.collect()
                except Exception as exc:
                    errors.append(exc)
            worker = threading.Thread(target=collect)
            worker.start()
            try:
                for _ in range(40):
                    result = vm.call(ids['demo.main'], [])
                    with vm.heap.pin(result):
                        vm.heap.collect()
                        self.assertEqual([x.fields[0] for x in result], ['retained'] * 4)
            finally:
                stop.set()
                worker.join()
            self.assertFalse(errors)
            self.assertGreater(vm.heap.collections, 40)
            vm.heap.release_result()
            vm.heap.collect()
            self.assertLess(len(vm.heap.objects), 200)

    def test_long_lived_vm_reclaims_temporary_values(self):
        with tempfile.TemporaryDirectory() as root:
            spec = 'type Box {\n    value: i64\n}\n\nfn make(value: i64) -> Box\n'
            impl = 'fn make(value: i64) -> Box\n{\n    return Box { value: value }\n}\n'
            write_pkg(root, module(spec, impl), fmt=True)
            _, art, ids = build(root)
            vm = VM(art, root)
            for i in range(6000):
                result = vm.call(ids['demo.make'], [i])
                self.assertEqual(result.fields, [i])
            self.assertGreater(vm.heap.collections, 5)
            self.assertLess(len(vm.heap.objects), 1200)

    def test_native_only_allocations_trigger_collection(self):
        with tempfile.TemporaryDirectory() as root:
            write_pkg(root,module('fn values() -> list[i64]\n','fn values() -> list[i64]\n{\n    return core.list.range(0, 10)\n}\n',uses='use core.list { range }'),fmt=True)
            _,art,ids=build(root)
            vm=VM(art,root)
            native=vm.by_name['core.list.range']
            for _ in range(3000):
                self.assertEqual(vm.call(native,[0,10]),list(range(10)))
            self.assertGreater(vm.heap.collections,2)
            self.assertLess(len(vm.heap.objects),1100)


class BoundedState(unittest.TestCase):
    def test_pressure_does_not_reset_depleted_keys(self):
        from gopyt.limiter import Limiter
        limiter = Limiter(capacity=4)
        for key in 'abcd':
            self.assertTrue(limiter.allow(key, 1, 1000, 0))
        for i in range(10000):
            self.assertFalse(limiter.allow(str(i), 1, 1000, 0))
        self.assertFalse(limiter.allow('a', 1, 1000, 0.5))
        self.assertEqual(len(limiter.buckets), 4)
        self.assertTrue(limiter.allow('new', 1, 1000, 1.0))
        self.assertEqual(len(limiter.buckets), 1)
        self.assertTrue(all(isinstance(k, bytes) and len(k) == 32 for k in limiter.buckets))

    def test_full_bucket_pressure_does_not_rescan_before_refill(self):
        from gopyt.limiter import Limiter, Bucket
        limiter=Limiter(capacity=16)
        for index in range(16):
            limiter.allow(str(index),1,10000,0)
        calls=[]
        original=Bucket.available
        def counted(bucket,now):
            calls.append(now)
            return original(bucket,now)
        with patch.object(Bucket,'available',counted):
            for index in range(10000):
                self.assertFalse(limiter.allow('new'+str(index),1,10000,1))
        self.assertLessEqual(len(calls),16)

    def test_parameter_change_cannot_bypass_limit(self):
        from gopyt.limiter import Limiter
        limiter = Limiter()
        self.assertTrue(limiter.allow('one', 1, 10000, 0))
        self.assertFalse(limiter.allow('one', 100000, 1, 0.1))
        self.assertTrue(limiter.allow('one', 100000, 1, 10))

    def test_bloom_and_trace_dump_have_fixed_bounds(self):
        from gopyt.observe import Observe, replay
        obs = Observe(reservoir_k=7)
        for i in range(1000):
            obs.event('x' * 10000 + str(i), True, 1, 'job', 1)
        self.assertLessEqual(len(obs.reservoir.items), 7)
        self.assertTrue(all(len(json.loads(row)['tag'].encode()) <= 256 for row in obs.reservoir.items))
        self.assertEqual(len(obs.bloom.bits), 1024)
        obs.event('known', False, 0)
        self.assertTrue(obs.bloom.contains('known'))
        self.assertGreater(replay(obs.snapshot()), 0)
        with tempfile.TemporaryDirectory() as root:
            self.assertTrue(obs.dump(root))
            first = Path(root, 'build/traces').stat().st_size
            for _ in range(100):
                obs.dump(root)
            self.assertEqual(Path(root, 'build/traces').stat().st_size, first)
            self.assertEqual(len(json.loads(Path(root, 'build/traces').read_text())['rows']), 7)

    def test_vm_counts_nominal_failure_without_manual_note(self):
        with tempfile.TemporaryDirectory() as root:
            spec = 'type Denied {\n}\n\ntask deny() -> Denied\n    effects { time }\n'
            impl = 'task deny() -> Denied\n    effects { time }\n{\n    core.time.sleep_ms(1)\n    return Denied { }\n}\n'
            write_pkg(root, module(spec, impl, uses='use core.time { sleep_ms }'), fmt=True)
            _, art, ids = build(root)
            vm = VM(art, root)
            for _ in range(20):
                vm.call(ids['demo.deny'], [])
            self.assertTrue(vm.observe.cusum.alarm)
            self.assertEqual(vm.observe.fail, 20)
            rows = vm.observe.snapshot()
            self.assertTrue(all(row['tag'] == 'outcome:demo.Denied' for row in rows))
            self.assertTrue(all(row['task'] == 'demo.deny' for row in rows))


class Transactions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        write_pkg(self.root, module('fn one() -> i64\n', 'fn one() -> i64\n{\n    return 1\n}\n'))

    def tearDown(self):
        self.temp.cleanup()

    def test_process_death_after_each_write_recovers_complete_old_tree(self):
        from gopyt.transaction import guard, JOURNAL
        path = 'impl/demo.gopyt'
        before = Path(self.root, path).read_bytes()
        lock = Path(self.root, 'gopyt.lock').read_bytes()
        script = '''import os, sys
from gopyt.transaction import guard, commit
from gopyt.files import atomic_write
root, stop = sys.argv[1], int(sys.argv[2])
p = "impl/demo.gopyt"
before = open(root+"/"+p,"rb").read()
lock = open(root+"/gopyt.lock","rb").read()
count = 0
def writer(root,path,data):
 global count
 atomic_write(root,path,data)
 count += 1
 if count == stop: os._exit(77)
with guard(root):
 commit(root, [(p,before,before.replace(b"return 1", b"return 2")),("gopyt.lock",lock,b"partial-lock")], writer=writer)
'''
        for stop in (1, 2):
            proc = subprocess.run([sys.executable, '-c', script, self.root, str(stop)], capture_output=True)
            self.assertEqual(proc.returncode, 77, proc.stderr)
            self.assertTrue(Path(self.root, JOURNAL).exists())
            load_package(self.root)  # recovery is mandatory before loading
            self.assertEqual(Path(self.root, path).read_bytes(), before)
            self.assertEqual(Path(self.root, 'gopyt.lock').read_bytes(), lock)
            self.assertFalse(Path(self.root, JOURNAL).exists())
            with guard(self.root):
                pass  # second recovery is idempotent

    def test_external_edit_conflict_is_preserved(self):
        from gopyt.transaction import commit, guard, JOURNAL
        path = 'impl/demo.gopyt'
        before = Path(self.root, path).read_bytes()
        def interrupted(root, path, data):
            Path(root, path).write_bytes(b'external edit')
            raise OSError('injected')
        with self.assertRaises(CompileError):
            with guard(self.root):
                commit(self.root, [(path, before, b'new')], writer=interrupted)
        self.assertEqual(Path(self.root, path).read_bytes(), b'external edit')
        self.assertTrue(Path(self.root, JOURNAL).exists())
        with self.assertRaises(CompileError):
            load_package(self.root)

    def test_cooperating_reader_waits_until_commit(self):
        from gopyt.transaction import guard
        ready = threading.Event()
        done = threading.Event()
        with guard(self.root):
            def reader():
                ready.set()
                load_package(self.root)
                done.set()
            thread = threading.Thread(target=reader)
            thread.start()
            ready.wait(1)
            self.assertFalse(done.wait(0.05))
        thread.join(2)
        self.assertTrue(done.is_set())

    def test_committed_journal_is_cleaned_without_rollback(self):
        from gopyt import transaction
        from gopyt.files import atomic_write
        path='impl/demo.gopyt'
        before=Path(self.root,path).read_bytes()
        after=before.replace(b'return 1',b'return 2')
        with transaction.guard(self.root):
            with patch.object(transaction,'remove',side_effect=OSError('interrupted cleanup')):
                with self.assertRaises(OSError):
                    transaction.commit(self.root,[(path,before,after)])
            self.assertTrue(Path(self.root,transaction.JOURNAL).exists())
        # A committed transaction is durable even if cleanup did not finish.
        with transaction.guard(self.root):
            pass
        self.assertEqual(Path(self.root,path).read_bytes(),after)
        self.assertFalse(Path(self.root,transaction.JOURNAL).exists())

    def test_descriptor_access_rejects_a_flipping_symlink(self):
        from gopyt.files import regular_file
        with tempfile.TemporaryDirectory() as outside:
            Path(outside,'private').write_bytes(b'outside')
            folder=Path(self.root,'flip')
            safe=Path(self.root,'safe')
            safe.mkdir();(safe/'private').write_bytes(b'inside')
            safe.rename(folder)
            stop=threading.Event()
            def flip():
                for _ in range(500):
                    folder.rename(safe)
                    folder.symlink_to(outside,target_is_directory=True)
                    folder.unlink()
                    safe.rename(folder)
                stop.set()
            worker=threading.Thread(target=flip)
            worker.start()
            attempts=0
            try:
                while not stop.is_set() or attempts<500:
                    try:
                        with regular_file(self.root,'flip/private') as stream:
                            self.assertEqual(stream.read(),b'inside')
                    except OSError:
                        pass
                    attempts+=1
            finally:
                worker.join()

    def test_candidate_tampering_is_refused_before_live_writes(self):
        from gopyt import evolve
        from gopyt.test_evolve import EXAMPLE
        with tempfile.TemporaryDirectory() as temp:
            root=str(Path(temp,'auth'))
            shutil.copytree(EXAMPLE,root)
            before=Path(root,'impl/auth.gopyt').read_bytes()
            plan=evolve._prepare(root,2)
            self.assertEqual(plan.kind,'Prepared')
            candidate=Path(plan.message,'impl/auth.gopyt')
            candidate.write_text(candidate.read_text().replace('2, 3600000','1, 3600000'))
            result=evolve._apply(root,plan)
            self.assertEqual(result.kind,'EvolveError')
            self.assertEqual(Path(root,'impl/auth.gopyt').read_bytes(),before)

    def test_source_digest_must_match_bytes_that_were_parsed(self):
        from gopyt.manifest import package_digest
        expected={'gopyt.toml':Path(self.root,'gopyt.toml').read_bytes(),
                  'spec/demo.gopyt':Path(self.root,'spec/demo.gopyt').read_bytes(),
                  'impl/demo.gopyt':Path(self.root,'impl/demo.gopyt').read_bytes()}
        package_digest(self.root,expected)
        Path(self.root,'impl/demo.gopyt').write_text('module demo\n')
        with self.assertRaises(CompileError) as cm:
            package_digest(self.root,expected)
        self.assertEqual(cm.exception.diag.code,46)

    def test_checked_survivors_use_persistent_weights_and_tag_replay(self):
        from gopyt import evolve
        from gopyt.test_evolve import EXAMPLE
        with tempfile.TemporaryDirectory() as temp:
            root=str(Path(temp,'auth'))
            shutil.copytree(EXAMPLE,root)
            candidates=evolve.candidates(root,2)
            bad=[(path,text+'\nfn invalid() -> i64\n{\n    return true\n}\n') for path,text in candidates[0]]
            traces=[{'task':'auth.login','tag':'trap:1:auth.login','code':1} for _ in range(10)]
            with patch.object(evolve,'candidates',return_value=[bad,candidates[1]]):
                plan=evolve._prepare(root,2,traces=traces)
            self.assertEqual(plan.kind,'Prepared')
            self.assertIn('candidate_1',plan.message)
            state=json.loads(Path(root,'evolve/weights.json').read_text())
            self.assertEqual(state['weights'],[0.5,1.0,1.0,1.0])
            self.assertEqual(state['replay_rows'],10)
            self.assertGreater(state['baseline_peak'],8)
            self.assertEqual(len(traces),10)


class Authoring(unittest.TestCase):
    def repair(self, root, code):
        with self.assertRaises(CompileError) as cm:
            check_package(load_package(root), check_lockfile=False)
        diag = cm.exception.diag
        self.assertEqual(diag.code, code, diag)
        self.assertTrue(diag.repair)
        path = Path(root, diag.file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(diag.repair)
        return diag.repair

    def test_identity_and_constructor_are_determined(self):
        with tempfile.TemporaryDirectory() as root:
            spec = 'module demo\n\ntype Box {\n    value: i64\n}\n\nfn same(value: i64) -> i64\n    ensures result == value\n\nfn boxed(value: i64) -> Box\n    ensures result == Box { value: value }\n'
            write_pkg(root, {'spec/demo.gopyt': spec}, fmt=True)
            text = self.repair(root, 33)
            self.assertIn('return value', text)
            self.assertIn('return Box { value: value }', text)
            self.assertNotIn('unresolved', text)
            check_package(load_package(root), check_lockfile=False)

    def test_custom_trait_stub_has_all_members(self):
        with tempfile.TemporaryDirectory() as root:
            spec = 'module demo\n\ntype Box {\n    text: str\n}\n\ntrait Named {\n    fn name(value: Self) -> str\n}\n\nprovide Named for Box\n'
            write_pkg(root, {'spec/demo.gopyt': spec}, fmt=True)
            text = self.repair(root, 33)
            self.assertIn('unresolved named_box_name', text)
            with self.assertRaises(CompileError) as cm:
                check_package(load_package(root), check_lockfile=False)
            self.assertEqual(cm.exception.diag.code, 30)

    def test_missing_test_has_safe_canonical_file_repair(self):
        with tempfile.TemporaryDirectory() as root:
            spec = 'fn one() -> i64\n    requires open needs_test_one\n'
            impl = spec + '{\n    return 1\n}\n'
            write_pkg(root, module(spec, impl), fmt=True)
            text = self.repair(root, 65)
            self.assertIn('test one', text)
            self.assertIn('unresolved one', text)
            with self.assertRaises(CompileError) as cm:
                check_package(load_package(root), check_lockfile=False)
            self.assertEqual(cm.exception.diag.code, 30)

    def test_missing_member_repair_preserves_completed_body(self):
        with tempfile.TemporaryDirectory() as root:
            write_pkg(root, module('fn one() -> i64\n\nfn two() -> i64\n', 'fn one() -> i64\n{\n    return 777\n}\n'), fmt=True)
            text = self.repair(root, 33)
            self.assertIn('return 777', text)
            self.assertIn('unresolved two', text)

    def test_derived_from_str_cannot_be_overridden(self):
        with tempfile.TemporaryDirectory() as root:
            spec = 'module demo\n\nuse core.convert { FromStr }\n\ntype Box {\n    value: i64\n}\n\nprovide FromStr for Box\n'
            impl = 'module demo\n\nuse core.convert { FromStr }\nuse core.status { ConvertError }\n\nprovide FromStr for Box\n{\n    fn from_str(text: str) -> Box | ConvertError\n    {\n        return Box { value: 999 }\n    }\n}\n'
            write_pkg(root, {'spec/demo.gopyt': spec, 'impl/demo.gopyt': impl}, fmt=True)
            with self.assertRaises(CompileError) as cm:
                check_package(load_package(root), check_lockfile=False)
            self.assertEqual(cm.exception.diag.code, 98)

    def test_serve_body_is_compiler_determined(self):
        from gopyt.test_vm import API_FILES
        with tempfile.TemporaryDirectory() as root:
            files = {path: text for path, text in API_FILES.items() if path.startswith('spec/')}
            write_pkg(root, files, fmt=True)
            text = self.repair(root, 33)
            self.assertIn('return net.http.serve()', text)
            self.assertIn('unresolved get_echo', text)

    def test_serve_cannot_add_an_arbitrary_body(self):
        from gopyt.test_vm import API_FILES
        with tempfile.TemporaryDirectory() as root:
            files=dict(API_FILES)
            files['impl/api.gopyt']=files['impl/api.gopyt'].replace('    return net.http.serve()', '    core.log.write("extra")\n    return net.http.serve()')
            write_pkg(root,files,fmt=True)
            with self.assertRaises(CompileError) as cm:
                check_package(load_package(root),check_lockfile=False)
            self.assertEqual(cm.exception.diag.code,93)


class ArtifactPolicy(unittest.TestCase):
    def test_evolve_round_trip_and_validation(self):
        art = G.Artifact(consts=[G.Const(G.TAG_STR, 'demo')], evolve=[G.EvolveEntry(0, 4, 1000, 8)])
        loaded = G.decode(G.encode(art))
        vm = VM(loaded)
        self.assertEqual(vm.evolve_bounds('demo'), (4, 1000, 8))
        self.assertEqual(vm.observe.reservoir.k, 8)
        for entry in (G.EvolveEntry(0, 0, 1000, 8), G.EvolveEntry(99, 4, 1000, 8)):
            with self.subTest(entry=entry), self.assertRaises(CompileError):
                G.decode(G.encode(G.Artifact(consts=art.consts, evolve=[entry])))

    def test_reset_parameters_and_load_after_reset_are_rejected(self):
        for arity, code in ((1, bytes([O.RESET_LOCAL])+struct.pack('<H',0)+bytes([O.UNIT,O.RETURN])),
                            (0, bytes([O.RESET_LOCAL])+struct.pack('<H',0)+bytes([O.LOAD_LOCAL])+struct.pack('<H',0)+bytes([O.RETURN]))):
            art = G.Artifact(consts=[G.Const(G.TAG_STR,'demo.one')], texprs=[G.TExpr(G.TE_UNIT)])
            art.funcs=[G.Func(0,O.KIND_FN,arity,1,0,[0]*arity,0,[0]*(1-arity),code)]
            with self.assertRaises(CompileError):
                G.decode(G.encode(art))

    def test_back_edge_cannot_exempt_a_double_store(self):
        # A never-taken back edge used to exempt every store in its interval.
        code = bytearray([O.UNIT,O.STORE_LOCAL,0,0,O.UNIT,O.STORE_LOCAL,0,0])
        code += bytes([O.CONST])+struct.pack('<I',1)
        code += bytes([O.JUMP_IF_TRUE])+struct.pack('<i',-18)
        code += bytes([O.UNIT,O.RETURN])
        art=G.Artifact(consts=[G.Const(G.TAG_STR,'demo.one'),G.Const(G.TAG_BOOL,False)], texprs=[G.TExpr(G.TE_UNIT)])
        art.funcs=[G.Func(0,O.KIND_FN,0,1,0,[],0,[0],bytes(code))]
        vm=VM(G.decode(G.encode(art)))
        with self.assertRaises(Trap) as cm:
            vm.call(0,[])
        self.assertEqual(cm.exception.code,10)

    def test_multiple_agents_require_identical_bounds(self):
        from gopyt.test_evolve import EXAMPLE, ThroughTheVm
        # Reuse the actual evolve package setup, then add a second agent.
        case=ThroughTheVm('test_check_accepts_the_evolve_agent')
        case.setUp()
        try:
            spec=Path(case.root,'spec/auth.gopyt')
            extra=ThroughTheVm.AGENT.split('\ntask harden()')[0].replace('agent Warden','agent Second')
            baseline=spec.read_text()
            spec.write_text(baseline+extra.replace('max 2','max 3'))
            with self.assertRaises(CompileError) as cm:
                check_package(load_package(case.root),check_fmt=False,check_lockfile=False)
            self.assertEqual(cm.exception.diag.code,117)
            spec.write_text(baseline+extra)
            prog=check_package(load_package(case.root),check_fmt=False,check_lockfile=False)
            self.assertEqual(prog.evolve['auth'],(2,30000,32))
            spec.write_text(baseline.replace('max 2','max 4294967296'))
            with self.assertRaises(CompileError) as cm:
                check_package(load_package(case.root),check_fmt=False,check_lockfile=False)
            self.assertEqual(cm.exception.diag.code,117)
        finally:
            case.tearDown()

    def test_rich_artifact_mutations_never_raise_host_exceptions(self):
        import random
        from gopyt.test_evolve import EXAMPLE
        with tempfile.TemporaryDirectory() as temp:
            root=str(Path(temp,'auth'))
            shutil.copytree(EXAMPLE,root)
            _,art,_=build(root)
            data=G.encode(art)
            rng=random.Random(915061)
            for _ in range(2000):
                mutant=bytearray(data)
                for _ in range(rng.randrange(1,5)):
                    mutant[rng.randrange(len(mutant))]=rng.randrange(256)
                try:
                    G.decode(bytes(mutant))
                except CompileError as exc:
                    self.assertEqual(exc.diag.code,100)

    def test_duplicate_union_types_with_different_indices_are_rejected(self):
        art=G.Artifact(texprs=[G.TExpr(G.TE_I64),G.TExpr(G.TE_I64),G.TExpr(G.TE_UNION,members=(0,1))])
        with self.assertRaises(CompileError):
            G.decode(G.encode(art))


class NetworkBoundary(unittest.TestCase):
    def test_real_tls_verification_and_model_protocol(self):
        import ssl
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        with tempfile.TemporaryDirectory() as root:
            certificate = Path(root, 'cert.pem')
            key = Path(root, 'key.pem')
            config = Path(root, 'cert.cnf')
            config.write_text('[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\n')
            proc = subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                '-keyout',str(key),'-out',str(certificate),'-config',str(config)], capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())
            hits = []
            class Reply(BaseHTTPRequestHandler):
                def log_message(self, *args): pass
                def do_POST(self):
                    hits.append(self.rfile.read(int(self.headers['Content-Length'])))
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"text":"verified TLS"}')
            server = ThreadingHTTPServer(('127.0.0.1',0),Reply)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certificate,key)
            server.socket = context.wrap_socket(server.socket,server_side=True)
            thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01))
            thread.start()
            origin = f'https://127.0.0.1:{server.server_port}'
            try:
                package = str(Path(root,'package'))
                Path(package).mkdir()
                header = 'task main() -> str | ModelError\n    effects { network, model }\n'
                write_pkg(package, module(header+f'\negress {{ "{origin}" }}\n',
                    header+'{\n    return core.model.complete("tls probe")\n}\n',
                    uses='use core.model { complete }\nuse core.status { ModelError }',
                    spec_uses='use core.status { ModelError }'),fmt=True)
                with patch.dict(os.environ,{'GOPYT_MODEL_URL':origin,'SSL_CERT_FILE':str(Path(root,'absent.pem'))}):
                    status,out = run_cli(package,'run','demo.main')
                self.assertEqual(status,0)
                self.assertIn('ModelError',out)
                self.assertEqual(hits,[])
                with patch.dict(os.environ,{'GOPYT_MODEL_URL':origin,'SSL_CERT_FILE':str(certificate)}):
                    status,out = run_cli(package,'run','demo.main')
                self.assertEqual((status,out),(0,'{"str":"verified TLS"}\n'))
                self.assertEqual(hits,[b'{"prompt":"tls probe"}'])
            finally:
                server.shutdown(); server.server_close(); thread.join()

    def test_ambient_proxy_does_not_receive_allowed_request(self):
        from http.server import BaseHTTPRequestHandler
        from gopyt.test_audit import http_endpoint
        hits=[]
        origin_bodies=[]
        class Proxy(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                hits.append(self.path)
                payload=b'{"text":"proxy"}'
                self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        class Origin(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                # Consume the request before closing: unread bytes can cause TCP RST.
                origin_bodies.append(self.rfile.read(int(self.headers['Content-Length'])))
                payload=b'{"text":"direct"}'
                self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        with http_endpoint(Proxy) as proxy, http_endpoint(Origin) as origin, tempfile.TemporaryDirectory() as root:
            header='task main() -> str | ModelError\n    effects { network, model }\n'
            write_pkg(root,module(header+f'\negress {{ "{origin}" }}\n',header+'{\n    return core.model.complete("probe")\n}\n',
                uses='use core.model { complete }\nuse core.status { ModelError }',spec_uses='use core.status { ModelError }'),fmt=True)
            from gopyt import natives
            import traceback
            failures=[]
            status=natives._status
            def capture(vm,name,*fields):
                if name=='ModelError' and fields==('network',):
                    failures.append(''.join(traceback.format_exception(*sys.exc_info())))
                return status(vm,name,*fields)
            with patch.dict(os.environ,{'http_proxy':proxy,'HTTP_PROXY':proxy,'no_proxy':'','NO_PROXY':'','GOPYT_MODEL_URL':origin}), patch.object(natives,'_status',capture):
                self.assertEqual(run_cli(root,'run','demo.main'),(0,'{"str":"direct"}\n'),failures)
            self.assertEqual(hits,[])
            self.assertEqual(origin_bodies,[b'{"prompt":"probe"}'])


if __name__ == '__main__':
    unittest.main()
