import json,os,sys,tempfile,hashlib
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,'/home/soultransit/devtony/gopyt-milestone-work')
from gopyt.storage import Store,StorageError
from gopyt.toolchain import FINGERPRINT
with tempfile.TemporaryDirectory() as temporary:
 base=Path(temporary).resolve();root=base/'app';root.mkdir();anchor=base/'anchor';anchor.mkdir(mode=0o700)
 a,b=os.urandom(32).hex(),os.urandom(32).hex();rings={}
 for name,active,keys in [('old','a',{'a':a,'b':b}),('new','b',{'a':a,'b':b}),('retired','b',{'b':b})]:
  path=base/name;path.write_text(json.dumps({'version':1,'active':active,'keys':keys}));path.chmod(0o600);rings[name]=str(path)
 env={'GOPYT_SECURITY_PROFILE':'strict','GOPYT_STORE_KEY_FILE':'','GOPYT_STORE_KEYRING_FILE':rings['old'],'GOPYT_STORE_ID':'stale-writer-probe','GOPYT_STORE_ANCHOR_DIR':str(anchor)}
 with patch.dict(os.environ,env):
  Store(root).enroll_anchor();Store(root).put('balance','10')
  with patch.dict(os.environ,{'GOPYT_STORE_KEYRING_FILE':rings['new']}):Store(root).rekey()
  with patch.dict(os.environ,{'GOPYT_STORE_KEYRING_FILE':rings['retired']}):before=Store(root).get('balance')
  Store(root).put('balance','9')
  with patch.dict(os.environ,{'GOPYT_STORE_KEYRING_FILE':rings['retired']}):
   try:after=Store(root).get('balance')
   except StorageError:after='StorageError'
 result={'runtime_sha256':FINGERPRINT.hex(),'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'fresh_new_key_read_before_stale_write':before,'stale_writer_published':True,'fresh_new_key_read_after_stale_write':after,'authority_generation':json.loads((anchor/'record.json').read_text())['generation'],'scope':'Functional stale active-key reproduction with trusted generation authority; no key material retained.'}
 Path(sys.argv[1]).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
