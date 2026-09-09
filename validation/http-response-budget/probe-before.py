import hashlib,json
from pathlib import Path
from unittest.mock import patch
from gopyt import test_app_runtime as fixture
from gopyt.toolchain import FINGERPRINT

def main():
    payload='x'*(8*1024*1024+1)
    files={p:s.replace('amount: i64','amount: str').replace('amount: core.str.len(name)','amount: name').replace('use core.str { len }\n','') for p,s in fixture.fixtures.API_FILES.items()}
    with patch.object(fixture.fixtures,'API_FILES',files), fixture.running_server(MAX_HANDLERS=1) as (vm,port):
        original=vm.call
        def call(fn,args,*rest):
            if vm.names[fn]=='api.get_echo':args=[payload]
            return original(fn,args,*rest)
        with patch.object(vm,'call',call):status,body=fixture.request(port)
    report={'runtime_sha256':FINGERPRINT.hex(),'payload_bytes':len(payload),'status':status,'response_bytes':len(body),'response_sha256':hashlib.sha256(body).hexdigest(),'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    Path('/tmp/gopyt-response-before.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
if __name__=='__main__':main()
