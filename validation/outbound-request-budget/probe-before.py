import hashlib,json
from pathlib import Path
from unittest.mock import patch
from gopyt import test_resource_authority as fixtures
from gopyt.toolchain import FINGERPRINT
from gopyt.values import EnumVal,Record

def main():
    fixture=fixtures.NetworkCalls();fixture.setUp()
    observed=[]
    original=fixture.server.RequestHandlerClass.do_POST
    def post(handler):
        observed.append(int(handler.headers.get('Content-Length','0')))
        return original(handler)
    body=b'x'*(1_048_576+1)
    try:
        vm=fixture.vm
        method=EnumVal(vm.type_id_of('net.http.HttpMethod'),1,[])
        request=Record(vm.type_id_of('net.http.HttpRequest'),[method,fixture.origin+'/body',body])
        with patch.object(fixture.server.RequestHandlerClass,'do_POST',post):
            response=vm.call(fixture.ids['remote.fetch'],[request])
        report={'runtime_sha256':FINGERPRINT.hex(),'payload_bytes':len(body),'server_received_lengths':observed,'result_type':vm.type_name(response.type_id),'status':response.fields[0],'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    finally:fixture.doCleanups()
    Path('/tmp/gopyt-outbound-body-before.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main()
