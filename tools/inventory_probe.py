"""Independent model, real HTTP acceptance and inventory workload measurements."""
import argparse
import concurrent.futures
import copy
import http.client
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from tools.ticket_probe import Server as TicketServer, Client

ROOT=Path(__file__).resolve().parents[1]


def model(state, req, now):
    state=copy.deepcopy(state)
    for item in state['entries']:
        if item['status']=='active' and item['expires_ms']<=now:item['status']='expired'
    def reply(outcome):return {'outcome':outcome,'state':state}
    action=req['action']
    if action=='reap':return reply('ok' if req['id']=='' and req['quantity']==0 and req['ttl_ms']==0 else 'invalid')
    if action!='reserve' and (req['quantity']!=0 or req['ttl_ms']!=0):return reply('invalid')
    if not 1<=len(req['id'])<=64 or action not in ('reserve','cancel','confirm'):return reply('invalid')
    if action=='reserve' and not (1<=req['quantity']<=10 and 1<=req['ttl_ms']<=60000):return reply('invalid')
    existing=next((e for e in state['entries'] if e['id']==req['id']),None)
    if existing:
        if action=='reserve':
            if existing['quantity']!=req['quantity'] or existing['ttl_ms']!=req['ttl_ms']:return reply('id_conflict')
            return reply(existing['status'])
        if existing['status']=='active':existing['status']='cancelled' if action=='cancel' else 'confirmed'
        return reply(existing['status'])
    if action!='reserve':return reply('not_found')
    if len(state['entries'])>=32:return reply('ledger_full')
    if sum(e['quantity'] for e in state['entries'] if e['status'] in ('active','confirmed'))+req['quantity']>10:return reply('sold_out')
    state['entries'].append(dict(id=req['id'],quantity=req['quantity'],expires_ms=now+req['ttl_ms'],ttl_ms=req['ttl_ms'],status='active'))
    return reply('active')


def command(action='reserve',ident='req',quantity=1,ttl=60000):
    return dict(action=action,id=ident,quantity=quantity if action=='reserve' else 0,ttl_ms=ttl if action=='reserve' else 0)


def invariant(state):
    entries=state['entries']
    assert len(entries)<=32 and len({e['id'] for e in entries})==len(entries)
    assert all(1<=e['quantity']<=10 and e['status'] in ('active','confirmed','cancelled','expired') for e in entries)
    assert 0<=sum(e['quantity'] for e in entries if e['status'] in ('active','confirmed'))<=10


class Server(TicketServer):
    def __init__(self,source=ROOT/'examples/inventory',shared_root=None):
        super().__init__(source,shared_root)
        self.headers={}
    def start(self):
        started=time.perf_counter()
        self.proc=subprocess.Popen([sys.executable,'-m','gopyt','run','inventory.serve'],cwd=self.root,env=self.env,stdout=self.log,stderr=self.log)
        while time.perf_counter()-started<30:
            if self.proc.poll() is not None:
                self.log.seek(0);raise AssertionError(self.log.read().decode())
            try:
                response=self.request('GET')
                if response['outcome']=='ok':return time.perf_counter()-started
            except (OSError,http.client.HTTPException):pass
            time.sleep(.01)
        raise AssertionError('inventory readiness timeout')
    def request(self,method='POST',body=None):
        client=Client(self.port)
        try:
            status,result=client.request(method,'/inventory',body,headers=self.headers)
            assert status==200,(status,result)
            assert set(result)=={'outcome','state'},result
            invariant(result['state'])
            return result
        finally:client.close()


def acceptance():
    checks=[]
    with Server() as server:
        server.command('check');server.start()
        reserve=command(ident='same',quantity=2)
        first=server.request(body=reserve);assert first['outcome']=='active'
        assert server.request(body=reserve)==first;checks.append('exact retry is idempotent')
        assert server.request(body=command(ident='same',quantity=3))['outcome']=='id_conflict'
        assert server.request(body=command(ident='same',quantity=2,ttl=40000))['outcome']=='id_conflict'
        checks.append('id rejects quantity and TTL changes')
        confirmed=server.request(body=command('confirm','same'));assert confirmed['outcome']=='confirmed'
        assert server.request(body=command('cancel','same'))==confirmed;checks.append('confirmation is terminal')
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            replies=list(pool.map(lambda i:server.request(body=command(ident=f'race{i}')),range(20)))
        winners=sum(r['outcome']=='active' for r in replies);assert winners==8,(winners,replies)
        state=server.request('GET')['state'];assert sum(e['quantity'] for e in state['entries'])==10
        checks.append('20 racing reservations cannot oversell')
        server.stop(abrupt=True);server.start();assert server.request('GET')['state']==state
        checks.append('acknowledged reservations survive SIGKILL restart')
    with Server() as server:
        server.start();old=server.request(body=command(ident='expire',quantity=10,ttl=20));assert old['outcome']=='active'
        time.sleep(.05)
        assert server.request(body=command(ident='new',quantity=10))['outcome']=='active'
        assert server.request(body=command(ident='expire',quantity=10,ttl=20))['outcome']=='expired'
        checks.append('expiry restores stock without reusing request ids')
    with Server() as server:
        server.start()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:server.request(body=command(ident='duplicate',quantity=10)),range(24)))
        assert all(r['outcome']=='active' for r in results)
        assert len(server.request('GET')['state']['entries'])==1
        checks.append('24 concurrent duplicate requests consume stock once')
        for i in range(4):
            server.stop(abrupt=True);server.start()
            assert server.request(body=command(ident='duplicate',quantity=10))['outcome']=='active'
        checks.append('four kill/retry cycles retain idempotency')
    return checks


def performance(source,requests=40,workers=1):
    with Server(source) as server:
        server.start();server.request(body=command(ident='load',quantity=1))
        def request(_):
            started=time.perf_counter();result=server.request('GET');assert result['outcome']=='ok'
            return time.perf_counter()-started
        started=time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:times=list(pool.map(request,range(requests)))
        elapsed=time.perf_counter()-started
        return {'requests':requests,'workers':workers,'seconds':elapsed,'requests_per_second':requests/elapsed,
                'median_seconds':statistics.median(times),'p95_seconds':sorted(times)[int(.95*(len(times)-1))],
                'raw_seconds':times,'final_state':server.request('GET')['state']}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    checks=acceptance();runs=[]
    for repeat in range(3):
        for variant in (['before','after'] if repeat%2==0 else ['after','before']):
            source=ROOT/('validation/baselines/inventory-unconditional-write' if variant=='before' else 'examples/inventory')
            result=performance(source);result.update(variant=variant,repeat=repeat);runs.append(result)
    load=performance(ROOT/'examples/inventory',requests=80,workers=8)
    assert load['p95_seconds']<2,load
    (args.out/'result.json').write_text(json.dumps({'completed':True,'checks':checks,'paired_runs':runs,'concurrent_load':load,
       'limits':'Local validation application. No payment provider, authentication, power-loss or sustained production throughput certification.'},indent=2)+'\n')
    print(json.dumps({'checks':len(checks),'load_p95':load['p95_seconds']}))


if __name__=='__main__':main()
