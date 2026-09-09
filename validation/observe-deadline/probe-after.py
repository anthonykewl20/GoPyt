import hashlib,json,threading,time
from pathlib import Path
from gopyt.test_native_admission import NativeAdmission
from gopyt.toolchain import FINGERPRINT
from gopyt.vm import Trap

def main():
    fixture=NativeAdmission();fixture.setUp()
    vm=fixture.vm
    started=threading.Event()
    original=vm.observe.task
    def task(name, **kwargs):
        started.set()
        return original(name, **kwargs)
    vm.observe.task=task
    report={'runtime_sha256':FINGERPRINT.hex(),'deadline_ms':100,'lock_hold_ms':300,
            'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    outcomes=[]
    def run():
        begin=time.monotonic_ns()
        vm.deadline_ns=begin+100_000_000
        try:
            vm.call(fixture.ids['demo.admit'],[])
            outcomes.append({'returned':True})
        except Trap as error:
            outcomes.append({'trap':error.code})
        finally:
            report['elapsed_ms']=(time.monotonic_ns()-begin)/1_000_000
            vm.deadline_ns=None
    vm.observe.lock.acquire()
    caller=threading.Thread(target=run)
    try:
        caller.start()
        assert started.wait(2)
        caller.join(.3)
        report['returned_before_lock_release']=not caller.is_alive()
    finally:
        vm.observe.lock.release()
        caller.join(5)
        report['caller_alive']=caller.is_alive()
        report['outcomes']=outcomes
        report['limiter_buckets']=len(vm.buckets)
        fixture.doCleanups()
    Path('/tmp/gopyt-observe-context-after.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))

if __name__=='__main__':main()
