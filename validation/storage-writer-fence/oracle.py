import hashlib,json,os,sqlite3,subprocess,sys,tempfile,zipfile
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
sys.path.insert(0,'/home/soultransit/devtony/gopyt-milestone-work')
from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV
from gopyt.storage import Store,StorageError
from gopyt.toolchain import FINGERPRINT
ROOT=Path('/home/soultransit/devtony/gopyt-milestone-work');out=Path(sys.argv[1]);out.mkdir(exist_ok=False)
inputs={'runtime_sha256':FINGERPRINT.hex(),'python':sys.version,'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'gopyt').glob('*.py')}}
(out/'inputs.json').write_text(json.dumps(inputs,indent=2)+'\n')
with tempfile.TemporaryDirectory() as temporary:
 base=Path(temporary).resolve();root=base/'app';root.mkdir();anchor=base/'anchor';anchor.mkdir(mode=0o700)
 old,new=os.urandom(32),os.urandom(32);rings={}
 for name,active,keys in [('old','a',{'a':old,'b':new}),('new','b',{'a':old,'b':new}),('retired','b',{'b':new})]:
  path=base/name;path.write_text(json.dumps({'version':1,'active':active,'keys':{k:v.hex() for k,v in keys.items()}}));path.chmod(0o600);rings[name]=str(path)
 env={'GOPYT_SECURITY_PROFILE':'strict','GOPYT_STORE_KEY_FILE':'','GOPYT_STORE_KEYRING_FILE':rings['old'],'GOPYT_STORE_ID':'fence-oracle','GOPYT_STORE_ANCHOR_DIR':str(anchor)}
 with patch.dict(os.environ,env):
  Store(root).enroll_anchor();Store(root).put('balance','10');snapshot=root/'.gopyt-state'/'store.sqlite3';backup=snapshot.read_bytes()
  with patch.dict(os.environ,{'GOPYT_STORE_KEYRING_FILE':rings['new']}):Store(root).fence_key(expected_generation=1)
  before=snapshot.read_bytes();record=json.loads((anchor/'record.json').read_text())
  rejected=False
  try:Store(root).put('balance','9')
  except StorageError:rejected=True
  assert rejected and snapshot.read_bytes()==before and json.loads((anchor/'record.json').read_text())==record
  assert record['generation']==2 and record['version']==2
  expected_writer=hashlib.sha256(b'GoPyt writer key\0'+new+b'GOPYT-SIV1\0fence-oracle').hexdigest();assert record['writer']==expected_writer
  with patch.dict(os.environ,{'GOPYT_STORE_KEYRING_FILE':rings['new']}):Store(root).restore_anchor(backup,expected_generation=2,reason='fenced backup recovery')
  raw=snapshot.read_bytes();magic=b'GOPYT-SIV1\0';n=len(magic);assert raw[:n]==magic
  plaintext=AESGCMSIV(new).decrypt(raw[n:n+12],raw[n+12:],magic+b'fence-oracle')
  with closing(sqlite3.connect(':memory:')) as db:
   db.deserialize(plaintext);rows=[list(row) for row in db.execute('SELECT key,value FROM kv ORDER BY key')]
  assert rows==[['balance','10']]
  record=json.loads((anchor/'record.json').read_text());assert record['generation']==3 and record['writer']==expected_writer and record['digest']==hashlib.sha256(raw).hexdigest()
  with patch.dict(os.environ,{'GOPYT_STORE_KEYRING_FILE':rings['retired']}):
   child=subprocess.run([sys.executable,'-c','from gopyt.storage import Store; import sys; assert Store(sys.argv[1]).get("balance")=="10"',str(root)],capture_output=True,text=True,cwd=ROOT);assert child.returncode==0,child.stderr
  wheel=Path('/tmp/gopyt-rollback-repro/0/gopyt-0.1.0-py3-none-any.whl')
  assert hashlib.sha256(wheel.read_bytes()).hexdigest()=='116ed1fdb2d9b4a73678a838cfed22776fe9a797ebea2bff9968aee457c5582e'
  installed=base/'old-runtime'
  with zipfile.ZipFile(wheel) as archive:
   assert all(not Path(name).is_absolute() and '..' not in Path(name).parts for name in archive.namelist())
   archive.extractall(installed)
  code='''import sys,json
sys.path.insert(0,sys.argv[1])
import gopyt.rollback as r
assert r.__file__.startswith(sys.argv[1])
try:r._decode(open(sys.argv[2],'rb').read(),'fence-oracle')
except r.SecurityError:print('v1-reader-rejected-v2')
else:raise AssertionError('old reader accepted writer policy it cannot enforce')
'''
  child=subprocess.run([sys.executable,'-c',code,str(installed),str(anchor/'record.json')],capture_output=True,text=True,cwd=ROOT);assert child.returncode==0,child.stderr;assert child.stdout.strip()=='v1-reader-rejected-v2'
  result={'status':'passed','stale_writer_rejected':rejected,'generation_after_restore':record['generation'],'rows':rows,'fresh_retired_key_process':True,'v1_reader_rejected_v2':True,'writer_identity':expected_writer,'scope':'Functional key-fencing, backup decryption and version-policy checks; no performance or power-loss claim.'}
 for name,sha in inputs['source_sha256'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha,name
 (out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
