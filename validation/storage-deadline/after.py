import hashlib,json,os,tempfile,threading,time
from pathlib import Path
from gopyt.testing import write_pkg
from gopyt.test_vm import module
from gopyt.cli import build
from gopyt.vm import VM,Trap
from gopyt.storage import Store
from gopyt.toolchain import FINGERPRINT,source_fingerprint

with tempfile.TemporaryDirectory() as temporary:
    root=os.path.realpath(temporary)
    signature='task write() -> unit | DbError\n    effects { database.write }\n'
    write_pkg(root,module(signature,signature+'{\n    return store.db.put("key", "committed")\n}\n',
        uses='use core.status { DbError }\nuse store.db { put }',spec_uses='use core.status { DbError }'),fmt=True)
    _,art,ids=build(root)
    vm=VM(art,root)
    vm.db._operation_lock.acquire()
    released=threading.Event()
    def unlock():
        time.sleep(.3)
        released.set()
        vm.db._operation_lock.release()
    worker=threading.Thread(target=unlock);worker.start()
    start=time.monotonic_ns();vm.deadline_ns=start+100_000_000
    try:
        vm.call(ids['demo.write'],[])
        outcome='returned'
    except Trap as error:
        outcome={'trap':error.code}
    finally:
        elapsed=(time.monotonic_ns()-start)/1_000_000
        vm.deadline_ns=None
        worker.join()
    report={'runtime_sha256':FINGERPRINT.hex(),'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'deadline_ms':100,'lock_release_ms':300,'elapsed_ms':elapsed,'outcome':outcome,
        'durable_value_after_timeout':Store(root).get('key')}
assert source_fingerprint(Path('gopyt'))==FINGERPRINT
Path('/tmp/gopyt-storage-deadline-after.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
