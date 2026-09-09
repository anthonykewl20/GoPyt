import hashlib,http.client,json,threading,time
from pathlib import Path
from unittest.mock import patch
from gopyt import test_app_runtime as fixture
from gopyt.toolchain import FINGERPRINT

def main():
    result=[]
    with patch('gopyt.server.MAX_KEEPALIVE_REQUESTS',2,create=True), fixture.running_server(MAX_HANDLERS=1) as (vm,port):
        first=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
        second=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
        def queued():
            second.request('GET','/echo/queued')
            response=second.getresponse();result.append((response.status,len(response.read())))
        first.request('GET','/echo/a');response=first.getresponse();response.read()
        worker=threading.Thread(target=queued);worker.start()
        try:
            until=time.monotonic()+2
            while vm.httpd.pending.qsize()!=1 and time.monotonic()<until:time.sleep(.001)
            assert vm.httpd.pending.qsize()==1
            first.request('GET','/echo/b');response=first.getresponse();response.read()
            closing=response.getheader('Connection')
            worker.join(.3)
            released=not worker.is_alive()
        finally:
            first.close();worker.join(3);second.close()
    report={'runtime_sha256':FINGERPRINT.hex(),'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'worker_limit':1,'request_quota':2,'final_connection_header':closing,'queued_client_served_before_peer_close':released,'queued_result':result}
    Path('/tmp/gopyt-keepalive-after.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main()
