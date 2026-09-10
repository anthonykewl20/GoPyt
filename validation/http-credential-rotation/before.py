import hashlib,http.client,json,os,secrets,sys,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,'/home/soultransit/devtony/gopyt-milestone-work')
from gopyt.test_app_runtime import running_server
from gopyt.toolchain import FINGERPRINT
with tempfile.TemporaryDirectory() as temporary:
 base=Path(temporary).resolve();token=base/'token';old,new=secrets.token_hex(24),secrets.token_hex(24)
 token.write_text(old);token.chmod(0o600)
 with patch.dict(os.environ,{'GOPYT_SECURITY_PROFILE':'development','GOPYT_HTTP_TOKEN_FILE':str(token),'GOPYT_STORE_ANCHOR_DIR':''}):
  with running_server(MAX_HANDLERS=2,QUEUE=4) as (vm,port):
   def request(value):
    c=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
    try:
     c.request('GET','/echo/abc',headers={'Authorization':'Bearer '+value});r=c.getresponse();r.read();return r.status
    finally:c.close()
   before=[request(old),request(new)]
   replacement=base/'next';replacement.write_text(new);replacement.chmod(0o600);replacement.replace(token)
   after=[request(old),request(new)]
 result={'runtime_sha256':FINGERPRINT.hex(),'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'before_old_new':before,'after_old_new':after,'scope':'One live listener and an atomic token-file replacement; no load/performance claim.'}
 Path(sys.argv[1]).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
