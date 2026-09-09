#!/usr/bin/env python3
"""Frozen cancellation/admission stress with worker, root and descriptor checks."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gopyt import ops
from gopyt.cli import build
from gopyt.testing import write_pkg
from gopyt.toolchain import FINGERPRINT, source_fingerprint
from gopyt.values import UNIT
from gopyt.vm import VM, Trap

SPEC = 'module probe\n\ntask run() -> list[unit]\n    effects { time }\n'
IMPL = '''module probe

use core.time { sleep_ms }

task run() -> list[unit]
    effects { time }
{
    return parallel max 4 timeout_ms 5 {
        core.time.sleep_ms(200)
        core.time.sleep_ms(200)
        core.time.sleep_ms(200)
        core.time.sleep_ms(200)
    }
}
'''


def metrics(vm):
    vm.heap.collect()
    descriptors = len(list(Path('/proc/self/fd').iterdir())) if Path('/proc/self/fd').is_dir() else None
    rss = None
    if Path('/proc/self/statm').is_file():
        rss = int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    return {'threads': len(threading.enumerate()), 'descriptors': descriptors, 'rss_bytes': rss,
            'worker_slots': vm.parallel_budget.active, 'heap_objects': len(vm.heap.objects),
            'frames': len(vm.heap.frames), 'pins': len(vm.heap.pins), 'handoffs': len(vm.heap.handoffs)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    tool_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'status': 'running', 'runtime_sha256': FINGERPRINT.hex(), 'tool_sha256': tool_digest,
              'workload_sha256': hashlib.sha256((SPEC + IMPL).encode()).hexdigest(),
              'python': sys.version, 'plan': {'sequential_timeouts': 100, 'concurrent_attempts': 256,
                                           'host_drivers': 8, 'vm_workers': 8, 'group_workers': 4,
                                           'deadline_ms': 5, 'native_sleep_ms': 200},
              'acceptance': '100 sequential timeout traps; concurrent outcomes only timeout/overload; zero idle worker/frame/pin/handoff counts, no additional live threads or descriptors; successful recovery call. RSS/latency are observations, not production SLOs.'}
    with args.output.open('x') as out:
        try:
            with tempfile.TemporaryDirectory() as root:
                write_pkg(root, {'spec/probe.gopyt': SPEC, 'impl/probe.gopyt': IMPL}, fmt=True)
                vm = VM(build(root)[1], root, parallel_workers=8)
                def attempt():
                    started = time.monotonic_ns()
                    try:
                        vm.call(vm.by_name['probe.run'], [])
                    except Trap as error:
                        code = error.code
                    else:
                        raise AssertionError('sleep workload unexpectedly completed')
                    return code, (time.monotonic_ns()-started)/1_000_000
                # Warm the same failure path before observing retained resources.
                assert attempt()[0] == ops.TRAP_TIMEOUT
                before = metrics(vm)
                sequential = [attempt() for _ in range(100)]
                assert all(code == ops.TRAP_TIMEOUT for code, _ in sequential)
                with ThreadPoolExecutor(max_workers=8) as pool:
                    concurrent = list(pool.map(lambda unused: attempt(), range(256)))
                assert all(code in (ops.TRAP_TIMEOUT, ops.TRAP_PAR_MAX) for code, _ in concurrent)
                self_id = vm.by_name['core.time.sleep_ms']
                assert vm.run_parallel([self_id], [0], 1, 1000) == [UNIT]
                vm.heap.release_result()
                after = metrics(vm)
                for field in ['worker_slots', 'frames', 'pins', 'handoffs']:
                    assert after[field] == 0, (field, after[field])
                assert after['threads'] == before['threads']
                if before['descriptors'] is not None:
                    assert after['descriptors'] == before['descriptors']
                assert vm.parallel_budget.peak <= 8
                report.update(before=before, after=after, worker_peak=vm.parallel_budget.peak,
                              rejected=vm.parallel_budget.rejected, recovery=True)
                for name, values in [('sequential', sequential), ('concurrent', concurrent)]:
                    durations = sorted(ms for _, ms in values)
                    report[name] = {'count': len(values), 'timeout': sum(code == ops.TRAP_TIMEOUT for code, _ in values),
                                    'overload': sum(code == ops.TRAP_PAR_MAX for code, _ in values),
                                    'latency_ms': {'p50': durations[len(durations)//2],
                                                   'p99': durations[min(len(durations)-1, int(len(durations)*0.99))],
                                                   'max': max(durations)}}
            assert source_fingerprint(ROOT/'gopyt') == FINGERPRINT
            assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == tool_digest
            report['status'] = 'passed'
        except BaseException as error:
            report['status'] = 'failed'
            report['error'] = type(error).__name__ + ': ' + str(error)
            raise
        finally:
            json.dump(report, out, indent=2)
            out.write('\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
