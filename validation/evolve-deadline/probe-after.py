import hashlib,json,multiprocessing,time
from pathlib import Path
from unittest.mock import patch
from gopyt.evolve import Outcome
from gopyt.test_native_admission import NativeAdmission
from gopyt.toolchain import FINGERPRINT,source_fingerprint
from gopyt.vm import Trap


def held_prepare(writer, root, max_candidates, module, traces):
    Path(root,'worker-started').write_text('started')
    time.sleep(.5)
    Path(root,'worker-finished').write_text('finished')
    try:
        writer.send(Outcome('NoChange'))
    finally:
        writer.close()


def main():
    fixture=NativeAdmission();fixture.setUp()
    report={'runtime_sha256':FINGERPRINT.hex(),'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'fixture_sha256':hashlib.sha256(Path('gopyt/test_native_admission.py').read_bytes()).hexdigest(),
            'deadline_ms':100,'injected_prepare_delay_ms':500}
    try:
        baseline={p.pid for p in multiprocessing.active_children()}
        source={str(p.relative_to(fixture.root)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in Path(fixture.root).rglob('*.gopyt')}
        start=time.monotonic_ns();fixture.vm.deadline_ns=start+100_000_000
        try:
            with patch('gopyt.evolve._prepare_worker',held_prepare):
                fixture.vm.call(fixture.ids['demo.harden'],[])
            outcome='returned'
        except Trap as error:
            outcome={'trap':error.code}
        finally:
            elapsed=(time.monotonic_ns()-start)/1_000_000
            fixture.vm.deadline_ns=None
        report.update({'elapsed_ms':elapsed,'outcome':outcome,
                       'worker_finished_before_return':Path(fixture.root,'worker-finished').exists(),
                       'in_flight_after_return':fixture.vm.evolve_in_flight,
                       'remaining_children':sorted(p.pid for p in multiprocessing.active_children() if p.pid not in baseline),
                       'source_unchanged':source=={str(p.relative_to(fixture.root)):hashlib.sha256(p.read_bytes()).hexdigest()
                                                  for p in Path(fixture.root).rglob('*.gopyt')}})
    finally:
        fixture.doCleanups()
    assert source_fingerprint(Path('gopyt'))==FINGERPRINT
    Path('/tmp/gopyt-evolve-context-after.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()
