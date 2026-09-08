"""Real invoice inputs through actual HTTP -> GoPyT -> SQLite -> restart.

Refund traces are generated scenarios, not observed payment or return history.
Run from repository root. Output must be new; never overwrites prior evidence.
"""
import argparse
import concurrent.futures
import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import secrets
import tempfile
import threading
import time

from gopyt.guard import canonical, digest, evaluate
from examples.guarded_refunds.freeze import HERE, bundle
from examples.guarded_refunds.service import Ledger, Server

DATA = Path(__file__).resolve().parent/'data'


def run(output, workers):
    output.mkdir(parents=True,exist_ok=False)
    raw=(DATA/'invoices.json').read_bytes()
    provenance=json.loads((DATA/'provenance.json').read_text())
    if digest(raw)!=provenance['normalized_sha256']:
        raise ValueError('normalized data hash mismatch')
    if digest((DATA/'online-retail.zip').read_bytes())!=provenance['archive_sha256']:
        raise ValueError('raw data hash mismatch')
    rows=json.loads(raw)
    frozen=bundle()
    pin=digest(frozen)
    (output/'bundle.json').write_bytes(frozen)
    (output/'provenance.json').write_bytes(canonical(provenance))
    receipt=evaluate(frozen,pin,HERE/'policy')
    (output/'gate.json').write_bytes(canonical(receipt))
    if not receipt['accepted']:
        raise ValueError('policy gate failed')
    started=time.monotonic()
    checks=0
    transcript=hashlib.sha256()
    with tempfile.TemporaryDirectory(prefix='refund-real-e2e-') as temp:
        db=Path(temp)/'ledger.sqlite'
        ledger=Ledger(db,HERE/'policy',frozen,pin)
        ledger.seed([{k:r[k] for k in ('id','customer','paid')} for r in rows])
        tokens={r['customer']:secrets.token_hex(32) for r in rows}
        credentials={token:customer for customer,token in tokens.items()}
        server=Server(ledger,credentials)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        def request(body):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=30)
            try:
                conn.request('POST','/refunds',canonical(body),{'Content-Type':'application/json','Authorization':'Bearer '+tokens[body['customer_id']]})
                response=conn.getresponse()
                return response.status,json.loads(response.read())
            finally:
                conn.close()
        def trace(row):
            records=[]
            def send(body,status):
                result=request(body)
                records.append({'request':body,'status':result[0],'response':result[1]})
                if result[0]!=status:
                    raise AssertionError((row['id'],body,result,status))
                return result
            amount=max(1,row['paid']//3)
            body={'order_id':row['id'],'customer_id':row['customer'],'amount':amount,
                  'expected_version':1,'idempotency_key':'partial'}
            first=send(body,200)
            if first[1]['refunded']!=amount or first[1]['remaining']!=row['paid']-amount or first[1]['version']!=2:
                raise AssertionError('wrong first transition')
            if send(body,200)!=first:
                raise AssertionError('retry changed result')
            send({**body,'amount':amount+1},409)
            version=2
            if row['paid']>amount:
                last=send({**body,'amount':row['paid']-amount,'expected_version':2,'idempotency_key':'rest'},200)
                version=3
                if last[1]['refunded']!=row['paid'] or last[1]['remaining']!=0 or last[1]['version']!=version:
                    raise AssertionError('wrong final transition')
            send({**body,'amount':1,'expected_version':version,'idempotency_key':'over'},409)
            return records,version
        versions={}
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                for count,(row,result) in enumerate(zip(rows,pool.map(trace,rows)),1):
                    records,version=result
                    versions[row['id']]=version
                    checks+=len(records)
                    transcript.update(canonical(records)+b'\n')
                    if count%1000==0:
                        print(json.dumps({'invoices_completed':count,'http_requests':checks}),flush=True)
            # Compare every durable row and every event count with a separate integer oracle.
            conn=sqlite3.connect(db)
            actual={r[0]:r[1:] for r in conn.execute('SELECT id,customer,paid,refunded,version FROM orders')}
            events=conn.execute('SELECT count(*) FROM requests').fetchone()[0]
            integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
            conn.close()
            expected={r['id']:(r['customer'],r['paid'],r['paid'],versions[r['id']]) for r in rows}
            if actual!=expected or events!=sum(v-1 for v in versions.values()) or integrity!='ok':
                raise AssertionError('durable ledger differs from independent oracle')
        finally:
            server.shutdown();server.server_close();thread.join();ledger.close()
        # Fresh service/VM and HTTP listener; replay across restart, including extreme amounts.
        ledger=Ledger(db,HERE/'policy',frozen,pin)
        server=Server(ledger,credentials)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            selected=sorted(rows,key=lambda r:r['paid'])[:16]+sorted(rows,key=lambda r:r['paid'])[-16:]
            for row in selected:
                amount=max(1,row['paid']//3)
                status,body=request({'order_id':row['id'],'customer_id':row['customer'],'amount':amount,
                                     'expected_version':1,'idempotency_key':'partial'})
                if status!=200 or body['refunded']!=amount or body['version']!=2:
                    raise AssertionError('restart lost idempotency')
                checks+=1
        finally:
            server.shutdown();server.server_close();thread.join();ledger.close()
        # Retain compact proof inputs and aggregate ledger state; do not expose a live service.
        conn=sqlite3.connect(db)
        conn.backup(sqlite3.connect(output/'ledger.sqlite'))
        conn.close()
    result={'schema':'gopyt.refunds.e2e.v1','passed':True,'input_rows_scanned':provenance['counts']['rows'],
            'eligible_invoices':len(rows),'http_requests':checks,'committed_refunds':events,
            'durable_rows_verified':len(actual),'restart_replays':len(selected),
            'transcript_sha256':transcript.hexdigest(),'bundle_sha256':pin,
            'data_sha256':digest(raw),'policy_gate_passed':receipt['passed'],
            'policy_gate_total':receipt['total'],'elapsed_seconds':round(time.monotonic()-started,3),
            'limitations':provenance['limits']+['Local loopback only, no authenticated payment integration.',
              'Finite deterministic scenarios; no measured human review-time or cross-language advantage.']}
    (output/'result.json').write_bytes(canonical(result)+b'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--workers',type=int,default=4)
    args=p.parse_args()
    if not 1<=args.workers<=16:p.error('workers must be 1..16')
    try:
        run(args.out,args.workers)
    except Exception as exc:
        if args.out.is_dir() and not (args.out/'result.json').exists():
            (args.out/'result.json').write_text(json.dumps({'passed':False,'error':repr(exc)})+'\n')
        raise
