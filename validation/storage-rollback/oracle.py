"""Real anchored-store operations; independent ciphertext/SQLite/state checks."""
import hashlib,json,os,sqlite3,sys,tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,'/home/soultransit/devtony/gopyt-milestone-work')
from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV
from gopyt.storage import Store,StorageError
from gopyt.toolchain import FINGERPRINT
ROOT=Path('/home/soultransit/devtony/gopyt-milestone-work')
out=Path(sys.argv[1]);out.mkdir(exist_ok=False)
identity={'runtime_sha256':FINGERPRINT.hex(),'python':sys.version,'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'gopyt').glob('*.py'))}}
(out/'inputs.json').write_text(json.dumps(identity,indent=2)+'\n')
with tempfile.TemporaryDirectory() as temporary:
 base=Path(temporary).resolve();root=base/'app';root.mkdir();anchor=base/'authority';anchor.mkdir(mode=0o700)
 key=os.urandom(32);keyfile=base/'key';keyfile.write_bytes(key);keyfile.chmod(0o600)
 env={'GOPYT_SECURITY_PROFILE':'strict','GOPYT_STORE_KEY_FILE':str(keyfile),'GOPYT_STORE_KEYRING_FILE':'','GOPYT_STORE_ID':'oracle-store','GOPYT_STORE_ANCHOR_DIR':str(anchor)}
 def inspect(expected_generation,expected_rows):
  record=json.loads((anchor/'record.json').read_bytes());raw=(root/'.gopyt-state'/'store.sqlite3').read_bytes()
  assert record['generation']==expected_generation
  assert record['digest']==hashlib.sha256(raw).hexdigest()
  magic=b'GOPYT-SIV1\0';offset=len(magic)
  assert raw[:offset]==magic
  plaintext=AESGCMSIV(key).decrypt(raw[offset:offset+12],raw[offset+12:],magic+b'oracle-store')
  with closing(sqlite3.connect(':memory:')) as db:
   db.deserialize(plaintext);rows=[list(row) for row in db.execute('SELECT key,value FROM kv ORDER BY key')]
  assert rows==expected_rows
  return {'generation':record['generation'],'digest':record['digest'],'rows':rows}
 observations=[]
 with patch.dict(os.environ,env):
  store=Store(root);store.enroll_anchor();store.put('balance','10');observations.append(inspect(1,[['balance','10']]))
  path=root/'.gopyt-state'/'store.sqlite3';backup=path.read_bytes();store.put('balance','9');observations.append(inspect(2,[['balance','9']]))
  path.write_bytes(backup)
  rejected=False
  try:Store(root).get('balance')
  except StorageError:rejected=True
  assert rejected
  store.restore_anchor(backup,expected_generation=2,reason='oracle recovery');observations.append(inspect(3,[['balance','10']]))
  receipt=json.loads((anchor/'restore-3.json').read_bytes());assert receipt['backup_digest']==hashlib.sha256(backup).hexdigest();assert receipt['previous_generation']==2
  store.rekey();observations.append(inspect(4,[['balance','10']]))
  rejected_wrong_oracle=False
  try:inspect(4,[['balance','9']])
  except AssertionError:rejected_wrong_oracle=True
  assert rejected_wrong_oracle
 result={'status':'passed','observations':observations,'replay_rejected':rejected,'receipt':receipt,'wrong_state_oracle_rejected':rejected_wrong_oracle,'scope':'Functional encrypted-state and generation checks, not throughput/power-loss/security-audit qualification.'}
 (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result,indent=2))
