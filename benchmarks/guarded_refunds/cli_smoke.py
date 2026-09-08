"""Exercise the documented CLI entry points in fresh actual processes."""
import http.client
import json
from pathlib import Path
import secrets
import selectors
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]


def run():
    with tempfile.TemporaryDirectory(prefix='refund-cli-') as temp:
        root=Path(temp)
        frozen=root/'bundle.json'
        freeze=subprocess.run([sys.executable,'-m','examples.guarded_refunds.freeze','--out',str(frozen)],
                              cwd=ROOT,capture_output=True,text=True,timeout=30,check=True)
        pin=freeze.stdout.strip()
        candidate=ROOT/'examples/guarded_refunds/policy'
        gate=subprocess.run([sys.executable,'-m','gopyt.guard','--bundle',str(frozen),
                             '--expected-sha256',pin,'--candidate',str(candidate),
                             '--receipt',str(root/'receipt.json')],cwd=ROOT,
                            capture_output=True,text=True,timeout=30,check=True)
        assert json.loads(gate.stdout)['accepted'] is True
        token=secrets.token_hex(32)
        (root/'credentials.json').write_text(json.dumps({token:'owner'}))
        (root/'seed.json').write_text(json.dumps([{'id':'invoice','customer':'owner','paid':13912}]))
        command=[sys.executable,'-m','examples.guarded_refunds.service','--candidate',str(candidate),
                 '--bundle',str(frozen),'--expected-sha256',pin,'--db',str(root/'ledger.sqlite'),
                 '--credentials',str(root/'credentials.json'),'--port','0']
        results=[]
        for launch in range(2):
            args=command+(['--seed',str(root/'seed.json')] if launch==0 else [])
            with open(root/f'server-{launch}.stderr','w') as errors:
                child=subprocess.Popen(args,cwd=ROOT,stdout=subprocess.PIPE,stderr=errors,text=True)
                try:
                    with selectors.DefaultSelector() as selector:
                        selector.register(child.stdout,selectors.EVENT_READ)
                        if not selector.select(timeout=30):
                            raise TimeoutError('service did not start')
                        line=child.stdout.readline().strip()
                    if not line.startswith('Local refund ledger: http://127.0.0.1:'):
                        raise AssertionError('service startup failed: '+line)
                    port=int(line.rsplit(':',1)[1])
                    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=10)
                    try:
                        body={'order_id':'invoice','customer_id':'owner','amount':3912,
                              'expected_version':1,'idempotency_key':'stable-retry'}
                        conn.request('POST','/refunds',json.dumps(body),
                                     {'Content-Type':'application/json','Authorization':'Bearer '+token})
                        response=conn.getresponse()
                        result=(response.status,json.loads(response.read()))
                        assert result[0]==200 and result[1]['remaining']==10000 and result[1]['version']==2
                        results.append(result)
                    finally:
                        conn.close()
                finally:
                    child.terminate()
                    try:child.wait(timeout=10)
                    except subprocess.TimeoutExpired:child.kill();child.wait(timeout=10)
                    child.stdout.close()
        assert results[0]==results[1]
        return {'passed':True,'entry_points':['freeze','guard','service'],
                'service_processes':2,'authenticated_http_requests':2,
                'replay_identical_after_process_restart':True,'bundle_sha256':pin}


if __name__=='__main__':
    print(json.dumps(run(),indent=2))
