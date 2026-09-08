#!/usr/bin/env python3
"""Real-HTTP retained-allocation diagnostic. Instrumented timings are not benchmarks.

Run only after performance jobs stop. Trace snapshots are captured inside an
isolated server process through stdin control, not an application endpoint.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import selectors
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
from ticket_probe import Client, envelope, fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, default=ROOT)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--batches', type=int, default=8)
    parser.add_argument('--reads-per-client', type=int, default=20)
    parser.add_argument('--clients', type=int, default=16)
    parser.add_argument('--records', type=int, default=64)
    parser.add_argument('--warm-workers', type=int, default=64)
    parser.add_argument('--idle-seconds', type=float, default=5)
    parser.add_argument('--trace-frames', type=int, default=8)
    args = parser.parse_args()
    if (min(args.batches,args.reads_per_client,args.clients,args.trace_frames,args.records,args.warm_workers)<1
            or max(args.clients,args.warm_workers)>64 or not 0<=args.idle_seconds<=60):
        parser.error('positive counts, clients <=64 and idle seconds in [0,60] required')
    args.runtime_root = args.runtime_root.resolve()
    source = (args.source or args.runtime_root/'examples/tickets').resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True,exist_ok=True)
    if any(args.output.iterdir()):
        parser.error('output must be empty to preserve earlier evidence')
    worker = Path(__file__).with_name('http_memory_worker.py')
    helpers = [Path(__file__),worker,ROOT/'tools/ticket_probe.py']
    helper_hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in helpers}
    runtime_hashes, source_hashes = fingerprint(args.runtime_root/'gopyt'),fingerprint(source)
    report = {'purpose':'instrumented real HTTP memory attribution, not throughput measurement or no-leak proof',
              'configuration':vars(args)|{'runtime_root':str(args.runtime_root),'source':str(source),'output':str(args.output)},
              'environment':{'python':sys.version,'executable':sys.executable,'platform':platform.platform(),
                             'cpu_count':os.cpu_count(),'load_average':os.getloadavg()},
              'runtime_sha256':runtime_hashes,'source_sha256':source_hashes,'harness_sha256':helper_hashes,
              'limitations':['tracemalloc and control sampling perturb time, allocations and RSS',
                             'tracemalloc does not account for all native SQLite/libc/thread allocations',
                             'explicit collections change natural execution and may hide transient accumulation',
                             'snapshots begin after build/imports and include instrumentation allocations',
                             'bounded fixed workload cannot prove absence of leaks or long-term stability',
                             'RSS and high-water RSS are Linux-specific observations, not assertions'],
              'checkpoints':[],'batches':[],'verified_reads':0,'passed':False}
    process = None
    clients = []

    def save():
        (args.output/'summary.json').write_text(json.dumps(report,indent=2)+'\n')

    def receive(timeout=120):
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout,selectors.EVENT_READ)
            if not selector.select(timeout):
                raise TimeoutError('worker control response timed out')
        line = process.stdout.readline()
        if not line:
            raise AssertionError(f'worker closed control stream, code={process.poll()}')
        return json.loads(line)

    def checkpoint(label):
        process.stdin.write(json.dumps({'op':'sample','label':label})+'\n')
        process.stdin.flush()
        row = receive()
        if row.get('label') != label:
            raise AssertionError('worker checkpoint mismatch')
        report['checkpoints'].append(row)
        save()
        print(f"{label}: RSS={row['after_python_gc']['rss_bytes']} traced={row['after_python_gc']['traced_current_bytes']} cells={row['after_python_gc']['heap']['cells']}",flush=True)

    def read(client,index):
        status, body = client.request('GET',f'/tickets/bench-{index%args.records}')
        expected = envelope('ok',f'bench-{index%args.records}',f'Title {index%args.records}','open',1)
        if status != 200 or body != expected or type(body.get('version')) is not int:
            raise AssertionError((status,body,expected))

    try:
        with tempfile.TemporaryDirectory(prefix='gopyt-http-memory-') as temp:
            project = Path(temp)/'tickets'
            shutil.copytree(source,project,ignore=shutil.ignore_patterns('build','evolve','.gopyt-state','.gopyt-transaction.lock','__pycache__'))
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0))
                port = sock.getsockname()[1]
            with (args.output/'server-stderr.txt').open('w') as errorlog:
                process = subprocess.Popen([sys.executable,str(worker),'--runtime-root',str(args.runtime_root),
                    '--project',str(project),'--output',str(args.output),'--port',str(port),
                    '--trace-frames',str(args.trace_frames)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                    stderr=errorlog,text=True,bufsize=1,env=dict(os.environ,PYTHONHASHSEED='0'))
                report['worker'] = receive()
                if not report['worker'].get('ready'):
                    raise AssertionError(report['worker'])
                seed = Client(port,timeout=120)
                try:
                    for i in range(args.records):
                        status,body=seed.request('POST','/tickets',{'id':f'bench-{i}','title':f'Title {i}'})
                        if status!=200 or body!=envelope('created',f'bench-{i}',f'Title {i}','open',1) or type(body.get('version')) is not int:
                            raise AssertionError((status,body))
                finally:
                    seed.close()
                report['verified_creates']=args.records
                checkpoint('seeded')
                # Each held connection occupies a distinct server worker. The
                # default 64 warms every configured worker; smaller values are
                # available for fast smoke checks and are recorded explicitly.
                warm = [Client(port,timeout=120) for _ in range(args.warm_workers)]
                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=args.warm_workers) as pool:
                        list(pool.map(lambda pair:read(*pair),zip(warm,range(args.warm_workers))))
                    checkpoint('all_workers_warm_connections_open')
                    if report['checkpoints'][-1]['after_python_gc']['server_connections']!=args.warm_workers:
                        raise AssertionError('worker warmup did not hold the declared connections')
                finally:
                    for client in warm:
                        client.close()
                report['verified_warm_reads']=args.warm_workers
                time.sleep(.1)
                checkpoint('workers_warm_closed')
                # Match the number of sampling/GC/snapshot operations in the
                # read phase, exposing growth caused by instrumentation alone.
                for batch in range(args.batches):
                    time.sleep(args.idle_seconds/args.batches)
                    checkpoint('idle_before_reads' if batch+1==args.batches else f'idle_before_{batch+1:03}')
                clients = [Client(port,timeout=120) for _ in range(args.clients)]
                for i,client in enumerate(clients):
                    read(client,i)
                report['verified_connection_warm_reads']=args.clients
                checkpoint('read_connections_warm')
                with concurrent.futures.ThreadPoolExecutor(max_workers=args.clients) as pool:
                    for batch in range(args.batches):
                        def work(pair):
                            i,client=pair
                            for j in range(args.reads_per_client):
                                read(client,batch*args.clients*args.reads_per_client+i*args.reads_per_client+j)
                            return args.reads_per_client
                        started=time.monotonic()
                        counts=list(pool.map(work,enumerate(clients)))
                        total=sum(counts)
                        report['verified_reads']+=total
                        report['batches'].append({'batch':batch+1,'verified_reads':total,'diagnostic_seconds':time.monotonic()-started})
                        checkpoint(f'reads_{batch+1:03}')
                for client in clients:
                    client.close()
                clients=[]
                time.sleep(.1)
                checkpoint('reads_closed')
                time.sleep(args.idle_seconds)
                checkpoint('idle_after_reads')
                process.stdin.write('{"op":"shutdown"}\n')
                process.stdin.flush()
                if receive()!= {'stopped':True}:
                    raise AssertionError('worker did not stop gracefully')
                process.wait(timeout=15)
                if process.returncode:
                    raise AssertionError(f'worker exit {process.returncode}')
                report['graceful_shutdown']=True
        # Analyze snapshots in the parent only after server exit, preventing
        # comparison/snapshot retention from contaminating the measured process.
        comparisons=[]
        for before,after in [('workers_warm_closed','idle_before_reads'),
                             ('read_connections_warm',f'reads_{args.batches:03}'),
                             ('workers_warm_closed','reads_closed'),
                             ('reads_closed','idle_after_reads')]:
            left=tracemalloc.Snapshot.load(str(args.output/(before+'.tracemalloc')))
            right=tracemalloc.Snapshot.load(str(args.output/(after+'.tracemalloc')))
            differences=right.compare_to(left,'traceback')
            comparisons.append({'before':before,'after':after,
                'net_traced_bytes':sum(x.size_diff for x in differences),
                'net_traced_blocks':sum(x.count_diff for x in differences),
                'largest_growth':[{'size_diff':x.size_diff,'count_diff':x.count_diff,
                    'size':x.size,'count':x.count,'traceback':x.traceback.format()} for x in differences if x.size_diff>0][:25],
                'largest_shrink':[{'size_diff':x.size_diff,'count_diff':x.count_diff,
                    'traceback':x.traceback.format()} for x in sorted(differences,key=lambda x:x.size_diff) if x.size_diff<0][:10]})
        report['allocation_comparisons']=comparisons
        if (fingerprint(args.runtime_root/'gopyt')!=runtime_hashes or fingerprint(source)!=source_hashes
                or {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in helpers}!=helper_hashes):
            raise AssertionError('runtime, app, or harness changed during diagnostic')
        report['artifacts_unchanged']=True
        report['passed']=True
    except BaseException as error:
        report['error']=repr(error)
        raise
    finally:
        for client in clients:
            client.close()
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=15)
        save()


if __name__=='__main__':
    main()
