#!/usr/bin/env python3
"""Private subprocess for http_memory_probe; control uses stdin, never HTTP."""
from __future__ import annotations

import argparse
from collections import Counter
import gc
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import threading
import time
import tracemalloc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', required=True, type=Path)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--trace-frames', default=8, type=int)
    args = parser.parse_args()
    sys.path.insert(0, str(args.runtime_root.resolve()))
    from gopyt.cli import build
    from gopyt.vm import VM
    import gopyt
    actual = Path(gopyt.__file__).resolve().parent
    if actual != (args.runtime_root/'gopyt').resolve():
        raise AssertionError(f'wrong runtime imported: {actual}')
    os.environ['GOPYT_HTTP_ADDR'] = f'127.0.0.1:{args.port}'
    _, artifact, ids = build(str(args.project))
    vm = VM(artifact, str(args.project))
    failures = []

    def run():
        try:
            vm.call(ids['tickets.serve'], [])
        except BaseException as exc:
            failures.append(repr(exc))
        finally:
            vm.heap.release_result()

    tracemalloc.start(args.trace_frames)
    server = threading.Thread(target=run, name='diagnostic-server', daemon=True)
    server.start()
    until = time.monotonic()+30
    while not hasattr(vm, 'httpd'):
        if failures or not server.is_alive() or time.monotonic() >= until:
            raise AssertionError(f'server failed startup: {failures}')
        time.sleep(.01)

    def emit(value):
        print(json.dumps(value), flush=True)

    def sample():
        with vm.heap.lock:
            heap = {'cells':len(vm.heap.objects), 'collections':vm.heap.collections,
                    'reclaimed':vm.heap.reclaimed, 'threshold':vm.heap.threshold,
                    'types':dict(Counter(type(x).__name__ for x in vm.heap.objects.values())),
                    'frames':len(vm.heap.frames), 'pins':len(vm.heap.pins),
                    'handoffs':len(vm.heap.handoffs)}
        with vm.httpd.connection_lock:
            connections = len(vm.httpd.connections)
        traced_current, traced_peak = tracemalloc.get_traced_memory()
        rss = int(Path('/proc/self/statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')
        return {'monotonic_ns':time.monotonic_ns(), 'rss_bytes':rss,
                'process_high_water_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                'traced_current_bytes':traced_current, 'traced_peak_bytes':traced_peak,
                'tracer_metadata_bytes':tracemalloc.get_tracemalloc_memory(),
                'heap':heap, 'python_gc_counts':gc.get_count(),
                'python_gc_stats':gc.get_stats(), 'python_thread_count':threading.active_count(),
                'os_thread_count':len(list(Path('/proc/self/task').iterdir())),
                'server_workers_alive':sum(t.is_alive() for t in vm.httpd.workers),
                'server_connections':connections, 'server_pending':vm.httpd.pending.qsize(),
                'telemetry_events':vm.observe.events, 'telemetry_memory_cells':vm.observe.memory_cells(),
                'telemetry_reservoir_entries':len(vm.observe.reservoir.items)}

    emit({'ready':True, 'pid':os.getpid(), 'runtime_path':str(actual),
          'artifact_sha256':hashlib.sha256((args.project/'build/out.gobyte').read_bytes()).hexdigest()})
    try:
        for line in sys.stdin:
            command = json.loads(line)
            if command['op'] == 'shutdown':
                vm.httpd.shutdown()
                server.join(timeout=15)
                if server.is_alive() or failures:
                    raise AssertionError(f'shutdown failed: {failures}')
                emit({'stopped':True})
                return
            if command['op'] != 'sample':
                raise ValueError('unknown command')
            label = command['label']
            if not label.replace('_','').isalnum():
                raise ValueError('invalid snapshot label')
            row = {'label':label, 'before_gc':sample()}
            snapshot = tracemalloc.take_snapshot()
            snapshot.dump(str(args.output/(label+'.before_gc.tracemalloc')))
            del snapshot
            if command.get('collect', True):
                row['vm_reclaimed'] = vm.heap.collect()
                row['after_vm_gc'] = sample()
                row['python_collected'] = gc.collect()
                row['after_python_gc'] = sample()
            snapshot = tracemalloc.take_snapshot()
            snapshot.dump(str(args.output/(label+'.tracemalloc')))
            del snapshot
            (args.output/(label+'.json')).write_text(json.dumps(row,indent=2)+'\n')
            emit(row)
    finally:
        if server.is_alive():
            vm.httpd.shutdown()
            server.join(timeout=15)


if __name__ == '__main__':
    main()
