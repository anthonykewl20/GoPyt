#!/usr/bin/env python3
"""Reproducible real-row ingestion through compiled GoPyT, with bounded retries."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from gopyt.cli import build
from gopyt.gobyte import decode
from gopyt.values import Record, Some, NONE
from gopyt.vm import VM


def source_hashes():
    paths=list((ROOT/'gopyt').glob('*.py'))+list((ROOT/'examples/retail_replay').glob('*/*.gopyt'))
    paths=[p for p in paths if not p.name.startswith('test_')]+[Path(__file__).resolve(),ROOT/'tools/retail_prepare.py']
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def chunks(data,size,limit):
    with (data/'rows.jsonl').open() as source:
        batch=[]
        for index,line in enumerate(source):
            if limit and index>=limit:break
            batch.append(json.loads(line))
            if len(batch)==size:yield batch;batch=[]
        if batch:yield batch


def machine(root):
    art=decode((root/'build/out.gobyte').read_bytes());vm=VM(art,str(root))
    ids={art.const_str(f.name):i for i,f in enumerate(art.funcs)}
    return vm,ids


def read(vm,ids,keys):
    result=vm.call(ids['retail.read'],[list(keys)])
    if not isinstance(result,Record) or vm.type_name(result.type_id)!='store.db.Snapshot':
        raise AssertionError('compiled snapshot read failed')
    # Copy scalar contents before another VM call. Embedding handles are not
    # implicitly roots across calls; do not retain a borrowed Record payload.
    return [value.value if isinstance(value,Some) else None for value in result.fields[0]]


def worker(args):
    vm,ids=machine(args.root);change_id=vm.type_id_of('store.db.Change')
    latencies=[];conflicts=applied=duplicates=rows=arithmetic=0
    started=time.perf_counter();cpu=time.process_time()
    for repetition in range(args.passes):
        for index,batch in enumerate(chunks(args.data,args.batch_rows,args.limit_rows)):
            if not args.overlap and index%args.workers!=args.worker_index:continue
            deltas=Counter()
            for _,_,sku,quantity in batch:deltas['retail/stock/'+sku]+=quantity
            marker='retail/batch/'+str(index)
            digest=hashlib.sha256(json.dumps(batch,separators=(',',':')).encode()).hexdigest()
            keys=sorted(deltas)+[marker];began=time.perf_counter()
            for attempt in range(args.max_retries):
                old=read(vm,ids,keys)
                if old[-1] is not None:
                    if old[-1]!=digest:raise AssertionError('batch identity mismatch')
                    duplicates+=1;break
                changes=[]
                for key,value in zip(keys[:-1],old[:-1]):
                    total=vm.call(ids['retail.add'],[int(value) if value is not None else 0,deltas[key]])
                    arithmetic+=1
                    changes.append(Record(change_id,[key,NONE if value is None else Some(value),Some(str(total))]))
                changes.append(Record(change_id,[marker,NONE,Some(digest)]))
                result=vm.call(ids['retail.commit'],[changes])
                if result is True:applied+=1;rows+=len(batch);break
                if result is not False:raise AssertionError('compiled transaction returned database error')
                conflicts+=1
                # Bounded backoff prevents a hot replay loop from starving peers.
                time.sleep(min(.00025*(attempt+1),.01))
            else:raise AssertionError('transaction retry budget exhausted')
            latencies.append((time.perf_counter()-began)*1000)
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'worker':args.worker_index,'applied_batches':applied,'duplicate_batches':duplicates,
            'source_rows_applied':rows,'conflicts':conflicts,'compiled_add_calls':arithmetic,
            'wall_seconds':time.perf_counter()-started,'cpu_seconds':time.process_time()-cpu,
            'peak_rss_bytes':int(peak if sys.platform=='darwin' else peak*1024),'latency_ms':latencies}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,required=True);parser.add_argument('--output',type=Path)
    parser.add_argument('--workers',type=int,default=4);parser.add_argument('--passes',type=int,default=2)
    parser.add_argument('--batch-rows',type=int,default=128);parser.add_argument('--limit-rows',type=int,default=0)
    parser.add_argument('--max-retries',type=int,default=1000);parser.add_argument('--encrypted',action='store_true')
    parser.add_argument('--overlap',action='store_true',help='every worker replays every batch to race identical requests')
    parser.add_argument('--worker-index',type=int,default=-1,help=argparse.SUPPRESS)
    parser.add_argument('--root',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args()
    if not 1<=args.workers<=16 or not 1<=args.batch_rows<=255 or not 1<=args.passes<=10 or args.limit_rows<0 or not 1<=args.max_retries<=1000:
        parser.error('invalid bounded workload configuration')
    if args.worker_index>=0:print(json.dumps(worker(args)));return
    if args.output is None:parser.error('--output is required')
    args.output.mkdir(parents=True,exist_ok=False)
    frozen=source_hashes();manifest=json.loads((args.data/'manifest.json').read_text())
    for name,digest in manifest['sha256'].items():
        with (args.data/name).open('rb') as source:
            if hashlib.file_digest(source,'sha256').hexdigest()!=digest:raise AssertionError('dataset changed')
    report={'kind':'public-retail-compiled-ingestion','complete':False,'source_sha256':frozen,
            'dataset':manifest,'configuration':{k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items() if k not in ('root','worker_index')},
            'environment':{'python':sys.version,'platform':platform.platform(),'cpu_count':os.cpu_count(),
                           'sqlite':sqlite3.sqlite_version,'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())},
            'limitations':['Public historical quantities, not deployed customer traffic or a payment workflow.',
                'Host streams and groups rows; arithmetic, consistent reads and commits run in compiled GoPyT.',
                'Ingestion batches may split an invoice; no whole-invoice business guarantee is asserted.',
                'Process restart verification is not a power-loss test. This is not beyond-RAM or HTTP throughput.',
                'Whole-snapshot 64 MiB backend retained; OS page cache is not flushed; shared host noise possible.']}
    processes=[]
    try:
        with tempfile.TemporaryDirectory(prefix='gopyt-retail-') as temporary:
            base=Path(temporary).resolve();root=base/'app';shutil.copytree(ROOT/'examples/retail_replay',root,ignore=shutil.ignore_patterns('build','.gopyt-state'))
            build(str(root));env=dict(os.environ,PYTHONPATH=str(ROOT))
            for name in ('GOPYT_STORE_KEY_FILE','GOPYT_STORE_KEYRING_FILE','GOPYT_DB_POLICY_FILE','GOPYT_STORE_ID'):
                env.pop(name,None)
            env['GOPYT_SECURITY_PROFILE']='strict' if args.encrypted else 'development'
            if args.encrypted:
                key=base/'key';key.write_bytes(os.urandom(32));key.chmod(0o600)
                env.update(GOPYT_STORE_KEY_FILE=str(key),GOPYT_STORE_ID='uci-retail-stress')
            policy=base/'policy.json';policy.write_text(json.dumps({'version':1,'read':['retail/'],'write':['retail/']}));policy.chmod(0o600)
            env['GOPYT_DB_POLICY_FILE']=str(policy)
            started=time.perf_counter()
            for i in range(args.workers):
                command=[sys.executable,str(Path(__file__).resolve()),'--worker-index',str(i),'--root',str(root),'--data',str(args.data),
                         '--workers',str(args.workers),'--passes',str(args.passes),'--batch-rows',str(args.batch_rows),
                         '--limit-rows',str(args.limit_rows),'--max-retries',str(args.max_retries)]
                if args.overlap:command.append('--overlap')
                processes.append(subprocess.Popen(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
            trials=[]
            for i,proc in enumerate(processes):
                out,err=proc.communicate(timeout=1800)
                (args.output/f'worker-{i}.stderr').write_text(err)
                if proc.returncode:raise AssertionError(f'worker {i} failed: {err[-2000:]}')
                trial=json.loads(out);trials.append(trial)
                (args.output/f'worker-{i}.json').write_text(json.dumps(trial,indent=2)+'\n')
            elapsed=time.perf_counter()-started
            # New VM and host Store after every writer process has exited.
            os.environ.update({k:v for k,v in env.items() if k.startswith('GOPYT_')})
            for name in ('GOPYT_STORE_KEY_FILE','GOPYT_STORE_KEYRING_FILE'):
                if name not in env:os.environ.pop(name,None)
            vm,ids=machine(root)
            if args.limit_rows:
                db=sqlite3.connect(args.data/'oracle.sqlite3')
                expected=dict(db.execute('SELECT sku,SUM(quantity) FROM source WHERE row<=? GROUP BY sku',(args.limit_rows+1,)));db.close()
            else:expected=json.loads((args.data/'expected.json').read_text())
            observed={};items=sorted(expected)
            for i in range(0,len(items),128):
                skus=items[i:i+128];values=read(vm,ids,['retail/stock/'+sku for sku in skus])
                for sku,value in zip(skus,values):observed[sku]=int(value) if value is not None else None
            if observed!=expected:raise AssertionError('final quantities differ from independent SQL oracle')
            latency=sorted(v for trial in trials for v in trial['latency_ms'])
            count=min(args.limit_rows or manifest['counts']['rows'],manifest['counts']['rows'])
            if sum(t['source_rows_applied'] for t in trials)!=count:raise AssertionError('source rows not applied exactly once')
            batches=(count+args.batch_rows-1)//args.batch_rows
            if sum(t['applied_batches'] for t in trials)!=batches:raise AssertionError('batch count mismatch')
            repetitions=args.passes*(args.workers if args.overlap else 1)
            if sum(t['duplicate_batches'] for t in trials)!=batches*(repetitions-1):raise AssertionError('retry idempotency count mismatch')
            report['result']={'source_rows':count,'sku_totals_verified':len(expected),'quantity_sum':sum(expected.values()),
                'wall_seconds_including_worker_startup_and_replay':elapsed,'source_rows_per_second':count/elapsed,
                'applied_batches':batches,'duplicate_batches':sum(t['duplicate_batches'] for t in trials),
                'conflicts':sum(t['conflicts'] for t in trials),'p50_batch_ms':statistics.median(latency),
                'p95_batch_ms':latency[min(len(latency)-1,int(len(latency)*.95))],
                'p99_batch_ms':latency[min(len(latency)-1,int(len(latency)*.99))],
                'max_worker_peak_rss_bytes':max(t['peak_rss_bytes'] for t in trials),
                'snapshot_bytes':(root/'.gopyt-state/store.sqlite3').stat().st_size,
                'oracle_equal':True,'restart_verified':True}
            if source_hashes()!=frozen:raise AssertionError('source changed during trial')
            report['complete']=True;print(json.dumps(report['result'],indent=2))
    except BaseException as exc:
        report['error']=repr(exc);raise
    finally:
        for proc in processes:
            if proc.poll() is None:proc.kill()
            proc.wait()
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
