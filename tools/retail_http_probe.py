"""Authenticated real HTTP checks for the compiled batch-store application."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.ticket_probe import Server as BaseServer, Client
from tools.retail_stress import chunks,source_hashes


class Server(BaseServer):
    def __init__(self):
        super().__init__(ROOT/'examples/retail_replay')
        base=self.root.parent.resolve()
        key=base/'key';key.write_bytes(os.urandom(32));key.chmod(0o600)
        token=base/'token';self.token=os.urandom(32).hex();token.write_text(self.token);token.chmod(0o600)
        policy=base/'policy.json';policy.write_text(json.dumps({'version':1,'read':['retail/'],'write':['retail/']}));policy.chmod(0o600)
        self.env.pop('GOPYT_STORE_KEYRING_FILE',None)
        self.env.update(GOPYT_SECURITY_PROFILE='strict',GOPYT_STORE_KEY_FILE=str(key),
                        GOPYT_STORE_ID='retail-http-test',GOPYT_HTTP_TOKEN_FILE=str(token),GOPYT_DB_POLICY_FILE=str(policy))

    def request(self,path,body,authorized=True):
        client=Client(self.port)
        try:return client.request('POST',path,body,headers={'Authorization':'Bearer '+self.token} if authorized else {})
        finally:client.close()

    def start(self):
        started=time.perf_counter()
        self.proc=subprocess.Popen([sys.executable,'-m','gopyt','run','retail.serve'],cwd=self.root,
                                   env=self.env,stdout=self.log,stderr=self.log)
        while time.perf_counter()-started<30:
            if self.proc.poll() is not None:
                self.log.seek(0);raise AssertionError(self.log.read().decode())
            try:
                status,result=self.request('/read',{'keys':['retail/readiness']})
                if status==200 and result=={'outcome':'ok','values':[None]}:return
            except (OSError,http.client.HTTPException):pass
            time.sleep(.01)
        raise AssertionError('retail HTTP readiness timeout')


def boundaries(server):
    checks=[]
    status,_=server.request('/commit',{'changes':[{'key':'retail/unauthorized','expected':None,'value':'bad'}]},False)
    assert status==401;checks.append('missing service token rejected before mutation')
    assert server.request('/read',{'keys':['retail/unauthorized']})[1]['values']==[None]
    assert server.request('/commit',{'changes':[{'key':'retail/bad','expected':4,'value':'bad'}]})[0]==400
    checks.append('invalid typed JSON rejected')
    result=server.request('/commit',{'changes':[{'key':'retail/allowed','expected':None,'value':'bad'},
                                               {'key':'other/denied','expected':None,'value':'bad'}]})
    assert result==(200,{'outcome':'db_error'}),result
    assert server.request('/read',{'keys':['retail/allowed']})[1]['values']==[None]
    checks.append('cross-namespace batch denied without partial write')
    changes={'changes':[{'key':'retail/race/a','expected':None,'value':'1'},
                        {'key':'retail/race/b','expected':None,'value':'1'}]}
    with ThreadPoolExecutor(max_workers=8) as pool:
        replies=list(pool.map(lambda _:server.request('/commit',changes),range(24)))
    assert sum(reply==(200,{'outcome':'committed'}) for reply in replies)==1,replies
    assert sum(reply==(200,{'outcome':'conflict'}) for reply in replies)==23,replies
    assert server.request('/read',{'keys':['retail/race/a','retail/race/b']})[1]['values']==['1','1']
    checks.append('24 duplicate HTTP transactions have exactly one winner')
    return checks


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--rows',type=int,default=4096);args=parser.parse_args()
    if not 1<=args.rows<=541909:parser.error('rows must be 1..541909')
    frozen=source_hashes();frozen['tools/retail_http_probe.py']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    manifest=json.loads((args.data/'manifest.json').read_text())
    for name,digest in manifest['sha256'].items():
        with (args.data/name).open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=digest:raise AssertionError('dataset changed')
    report={'complete':False,'source_sha256':frozen,'dataset':manifest,'source_rows':args.rows,
            'limitations':['HTTP quantities are calculated by the host driver; GoPyT executes typed decoding and transactions.',
                           'Loopback bearer authentication and encrypted snapshots; external TLS and end-user authorization not tested.']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as output:
        try:
            with Server() as server:
                server.start();checks=boundaries(server);started=time.perf_counter();batches=0
                for index,batch in enumerate(chunks(args.data,64,args.rows)):
                    deltas=Counter()
                    for _,_,sku,quantity in batch:deltas['retail/stock/'+sku]+=quantity
                    keys=sorted(deltas);status,result=server.request('/read',{'keys':keys})
                    assert status==200 and result['outcome']=='ok'
                    changes=[{'key':key,'expected':value,'value':str((int(value) if value is not None else 0)+deltas[key])}
                             for key,value in zip(keys,result['values'])]
                    changes.append({'key':'retail/batch/'+str(index),'expected':None,
                                    'value':hashlib.sha256(json.dumps(batch).encode()).hexdigest()})
                    assert server.request('/commit',{'changes':changes})==(200,{'outcome':'committed'})
                    assert server.request('/commit',{'changes':changes})==(200,{'outcome':'conflict'})
                    batches+=1
                report['ingestion_seconds']=time.perf_counter()-started
                server.stop(abrupt=True);server.start()
                with sqlite3.connect(args.data/'oracle.sqlite3') as db:
                    expected=dict(db.execute('SELECT sku,SUM(quantity) FROM source WHERE row<=? GROUP BY sku',(args.rows+1,)))
                skus=sorted(expected);observed={}
                for index in range(0,len(skus),128):
                    group=skus[index:index+128];status,result=server.request('/read',{'keys':['retail/stock/'+sku for sku in group]})
                    assert status==200 and result['outcome']=='ok'
                    observed.update({sku:int(value) if value is not None else None for sku,value in zip(group,result['values'])})
                assert observed==expected
                now=source_hashes();now['tools/retail_http_probe.py']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
                assert now==frozen
                report.update(complete=True,checks=checks,committed_batches=batches,stale_retries_rejected=batches,
                              sku_totals_verified=len(expected),quantity_sum=sum(expected.values()),kill_restart_oracle_equal=True)
        except BaseException as exc:report['error']=repr(exc);raise
        finally:output.write(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('source_sha256','dataset')},indent=2))


if __name__=='__main__':main()
