import hashlib,json,os,sys,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,'/home/soultransit/devtony/gopyt-milestone-work')
from gopyt.storage import Store,DIRECTORY,DATABASE
from gopyt.toolchain import FINGERPRINT
with tempfile.TemporaryDirectory() as temporary:
 base=Path(temporary).resolve();root=base/'app';root.mkdir();key=base/'key';key.write_bytes(os.urandom(32));key.chmod(0o600)
 with patch.dict(os.environ,{'GOPYT_SECURITY_PROFILE':'strict','GOPYT_STORE_KEY_FILE':str(key),'GOPYT_STORE_KEYRING_FILE':'','GOPYT_STORE_ID':'replay-probe'}):
  Store(root).put('balance','10');snapshot=root/DIRECTORY/DATABASE;old=snapshot.read_bytes()
  Store(root).put('balance','9');new=snapshot.read_bytes();before=Store(root).get('balance')
  snapshot.write_bytes(old);after=Store(root).get('balance')
  print(json.dumps({'runtime_sha256':FINGERPRINT.hex(),'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'before_replay':before,'after_replay':after,'older_ciphertext_sha256':hashlib.sha256(old).hexdigest(),'newer_ciphertext_sha256':hashlib.sha256(new).hexdigest(),'replay_accepted':after=='10'},indent=2))
