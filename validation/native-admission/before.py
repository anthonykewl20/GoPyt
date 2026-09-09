import hashlib,json,os,tempfile,threading,time
from pathlib import Path
from gopyt.testing import write_pkg
from gopyt.test_vm import module
from gopyt.cli import build
from gopyt.vm import VM,Trap
from gopyt.toolchain import FINGERPRINT,source_fingerprint

with tempfile.TemporaryDirectory() as temporary:
    root=os.path.realpath(temporary)
    signature='task admit() -> unit | Throttled\n    effects { time }\n'
    write_pkg(root,module(signature,signature+'{\n    return core.limit.allow("probe", 1, 100000)\n}\n',
        uses='use core.status { Throttled }\nuse core.limit { allow }',spec_uses='use core.status { Throttled }'),fmt=True)
    _,art,ids=build(root)
    vm=VM(art,root);vm.lock.acquire()
    def release():
        time.sleep(.3)
        vm.lock.release()
    worker=threading.Thread(target=release);worker.start()
    start=time.monotonic_ns();vm.deadline_ns=start+100_000_000
    try:
        vm.call(ids['demo.admit'],[])
        outcome='returned'
    except Trap as error:
        outcome={'trap':error.code}
    finally:
        elapsed=(time.monotonic_ns()-start)/1_000_000
        vm.deadline_ns=None
        worker.join()
    report={'runtime_sha256':FINGERPRINT.hex(),'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'deadline_ms':100,'lock_release_ms':300,'elapsed_ms':elapsed,'outcome':outcome,
        'buckets_after_timeout':[{'level':b.level,'tokens':b.tokens} for b in vm.buckets.values()]}
assert source_fingerprint(Path('gopyt'))==FINGERPRINT
Path('/tmp/gopyt-native-lock-before.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
