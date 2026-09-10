import hashlib,json,os,sqlite3,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path('/home/soultransit/devtony/gopyt-milestone-work')
sys.path.insert(0,str(ROOT))
from gopyt.storage import Store,DIRECTORY,DATABASE
from gopyt.toolchain import FINGERPRINT
from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'gopyt').glob('*.py')}
(out/'inputs.json').write_text(json.dumps({'runtime_sha256':FINGERPRINT.hex(),'python':sys.version,'source_sha256':sources,'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},indent=2)+'\n')
with tempfile.TemporaryDirectory() as temporary:
 base=Path(temporary).resolve();root=base/'app';root.mkdir();key=base/'key';material=os.urandom(32);key.write_bytes(material);key.chmod(0o600)
 environment={k:v for k,v in os.environ.items() if not k.startswith('GOPYT_')};environment.update(GOPYT_SECURITY_PROFILE='development',GOPYT_STORE_ID='migration-oracle')
 os.environ.clear();os.environ.update(environment)
 expected={f'row-{i:04}':f'value {i} Ω\n' for i in range(513)}
 store=Store(root)
 for k,v in expected.items():store.put(k,v)
 path=root/DIRECTORY/DATABASE;plain=path.read_bytes();digest=hashlib.sha256(plain).hexdigest();backup=base/'recovery'
 environment.update(GOPYT_SECURITY_PROFILE='strict',GOPYT_STORE_KEY_FILE=str(key));os.environ.update(environment)
 command=[sys.executable,'-m','gopyt.store_admin','migrate','--root',str(root),'--backup',str(backup),'--expected-digest',digest]
 result=subprocess.run(command,cwd=ROOT,env=environment,capture_output=True,text=True,timeout=20);assert result.returncode==0,result.stderr
 report=json.loads(result.stdout);cipher=path.read_bytes();assert cipher==backup.read_bytes()
 prefix=b'GOPYT-SIV1\0';decrypted=AESGCMSIV(material).decrypt(cipher[len(prefix):len(prefix)+12],cipher[len(prefix)+12:],prefix+b'migration-oracle')
 assert decrypted==plain
 db=sqlite3.connect(':memory:');db.deserialize(decrypted)
 assert dict(db.execute('SELECT key,value FROM kv'))==expected;db.close()
 wrong=command[:-1]+['0'*64];control=subprocess.run(wrong,cwd=ROOT,env=environment,capture_output=True,text=True,timeout=20);assert control.returncode==2 and path.read_bytes()==cipher
 path.unlink()
 recovered=subprocess.run(command,cwd=ROOT,env=environment,capture_output=True,text=True,timeout=20);assert recovered.returncode==0 and path.read_bytes()==cipher
 fresh=subprocess.run([sys.executable,'-c','from gopyt.storage import Store;import sys;assert Store(sys.argv[1]).get("row-0512")=="value 512 Ω\\n"',str(root)],cwd=ROOT,env=environment,capture_output=True,text=True,timeout=20);assert fresh.returncode==0,fresh.stderr
 result={'status':'passed','rows':len(expected),'exact_plaintext_preserved':True,'independent_rows_equal':True,'wrong_digest_rejected':True,'fresh_process_after_missing_file_recovery':True,'scope':'Functional Linux migration and recovery checks; no power-loss, performance or security-audit claim.'}
 for name,h in sources.items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==h
 (out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
