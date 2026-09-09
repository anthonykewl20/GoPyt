import json, tempfile, threading, time, os, hashlib
from pathlib import Path
from unittest.mock import patch
from gopyt.cli import build,make_vm
from gopyt.testing import write_pkg
from gopyt.test_vm import API_FILES
from gopyt.toolchain import FINGERPRINT,source_fingerprint
from gopyt.vm import Trap
report={'runtime_sha256':FINGERPRINT.hex(),'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'deadline_ms':300,'manual_shutdown_after_ms':600}
with tempfile.TemporaryDirectory() as root, patch('gopyt.server._addr',return_value=('127.0.0.1',0)), patch('gopyt.server.MAX_HANDLERS',2):
 write_pkg(root,API_FILES)
 prog,art,ids=build(root);vm=make_vm(root,prog,art,ids)
 outcome=[]
 def run():
  start=time.monotonic_ns();vm.deadline_ns=start+300_000_000
  try:vm.call(ids['api.serve'],[]);outcome.append({'return':'success'})
  except Trap as e:outcome.append({'trap':e.code})
  finally:report['elapsed_ms']=(time.monotonic_ns()-start)/1_000_000
 thread=threading.Thread(target=run);thread.start()
 try:
  time.sleep(.6)
  report['still_running_after_deadline']=thread.is_alive()
 finally:
  if getattr(vm,'httpd',None) is not None:vm.httpd.shutdown()
  thread.join(5)
 report['joined_after_manual_shutdown']=not thread.is_alive();report['outcome']=outcome;report['serving_after_cleanup']=vm.serving
assert source_fingerprint(Path.cwd() / "gopyt")==FINGERPRINT
Path('/tmp/gopyt-serve-deadline-before.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
