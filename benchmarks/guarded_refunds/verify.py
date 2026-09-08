"""Independently inspect a completed replay's retained DB after restart replays."""
import argparse
import json
from pathlib import Path
import sqlite3
from gopyt.guard import digest


def verify(run):
    result=json.loads((run/'result.json').read_text())
    if result.get('passed') is not True:
        raise ValueError('replay did not pass')
    data=Path(__file__).resolve().parent/'data/invoices.json'
    raw=data.read_bytes()
    if digest(raw)!=result['data_sha256']:
        raise ValueError('input identity changed')
    rows=json.loads(raw)
    policy_hash=json.loads((run/'gate.json').read_text())['candidate_sha256']
    conn=sqlite3.connect('file:'+str((run/'ledger.sqlite').resolve())+'?mode=ro',uri=True)
    try:
        integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
        orders={row[0]:row[1:] for row in conn.execute('SELECT id,customer,paid,refunded,version,closed FROM orders')}
        if set(orders)!={r['id'] for r in rows}:
            raise ValueError('wrong order inventory')
        checked=0
        for row in rows:
            first=max(1,row['paid']//3)
            steps=1 if first==row['paid'] else 2
            if orders[row['id']]!=(row['customer'],row['paid'],row['paid'],steps+1,0):
                raise ValueError('wrong persisted order state')
            records=conn.execute('SELECT key,payload,response FROM requests WHERE order_id=? ORDER BY key',(row['id'],)).fetchall()
            expected={'partial':(first,1,first)}
            if steps==2:expected['rest']=(row['paid']-first,2,row['paid'])
            if {r[0] for r in records}!=set(expected):
                raise ValueError('missing or extra idempotency receipt')
            for key,payload,response in records:
                body=json.loads(payload);reply=json.loads(response)
                amount,version,cumulative=expected[key]
                if body!={'order_id':row['id'],'customer_id':row['customer'],'amount':amount,
                          'expected_version':version,'idempotency_key':key}:
                    raise ValueError('wrong stored request')
                if reply!={'order_id':row['id'],'customer_id':row['customer'],'refunded':cumulative,
                           'remaining':row['paid']-cumulative,'version':version+1,'currency':'GBP',
                           'policy_sha256':policy_hash,
                           'bundle_sha256':result['bundle_sha256']}:
                    raise ValueError('wrong stored response')
                checked+=1
        if integrity!='ok' or checked!=result['committed_refunds']:
            raise ValueError('database integrity or event count mismatch')
    finally:
        conn.close()
    return {'passed':True,'orders_checked':len(rows),'stored_requests_and_responses_checked':checked,
            'ledger_sha256':digest((run/'ledger.sqlite').read_bytes()),
            'stage':'after all HTTP requests and restart replays',
            'scope':'Independent persisted-state inspection; uses the same normalized real inputs.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    args=parser.parse_args()
    result=verify(args.run)
    with open(args.run/'independent-verification.json','x') as handle:
        json.dump(result,handle,indent=2);handle.write('\n')
    print(json.dumps(result,indent=2))
