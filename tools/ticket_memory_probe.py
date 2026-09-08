#!/usr/bin/env python3
"""Bounded direct-VM retention diagnostic; never a no-leak or latency claim.

This separates tracked GoPyT heap cells from process resident memory. It excludes
HTTP server workers and cannot establish absence of a process or server leak.
Run independently, after performance measurements have stopped.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gc
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gopyt.cli import build
from gopyt.values import Record
from gopyt.vm import VM
from app_profile import require_reply
from ticket_probe import environment, fingerprint


def memory_sample(vm):
    rss = None
    try:
        resident_pages = int(Path('/proc/self/statm').read_text().split()[1])
        rss = resident_pages * os.sysconf('SC_PAGE_SIZE')
    except (OSError, ValueError, IndexError):
        pass
    maximum = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # getrusage uses bytes on macOS and KiB on Linux. Unsupported platforms
    # retain the raw value rather than applying a guessed conversion.
    maximum_bytes = maximum if sys.platform == 'darwin' else maximum * 1024 if sys.platform.startswith('linux') else None
    return {'rss_bytes': rss, 'process_high_water_rss_bytes': maximum_bytes,
            'process_high_water_raw': maximum, 'vm_heap_cells': len(vm.heap.objects),
            'vm_collection_count': vm.heap.collections,
            'vm_heap_types': dict(Counter(type(value).__name__ for value in vm.heap.objects.values())),
            'python_gc_counts': list(gc.get_count())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source', default=ROOT/'examples/tickets', type=Path)
    parser.add_argument('--batches', default=10, type=int)
    parser.add_argument('--calls-per-batch', default=1000, type=int)
    parser.add_argument('--heap-headroom', default=256, type=int,
                        help='Predeclared allowed post-collection cells above seeded baseline; never an RSS threshold')
    args = parser.parse_args()
    if args.batches < 1 or args.calls_per_batch < 1 or args.heap_headroom < 0:
        parser.error('batches/calls must be positive and heap-headroom nonnegative')
    args.output.mkdir(parents=True, exist_ok=True)
    source_hashes = fingerprint(args.source)
    compiler_hashes = fingerprint(ROOT/'gopyt')
    helpers = [Path(__file__), ROOT/'tools/ticket_probe.py', ROOT/'tools/app_profile.py']
    helper_hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in helpers}
    report = {'purpose': 'direct VM retention diagnostic, not benchmark or proof of no leak',
              'environment': environment(), 'source_sha256': source_hashes,
              'compiler_sha256': compiler_hashes, 'tool_and_helpers_sha256': helper_hashes,
              'configuration': {'batches':args.batches, 'calls_per_batch':args.calls_per_batch,
                                'seed_records':64, 'heap_headroom_cells':args.heap_headroom,
                                'target':'tickets.retrieve', 'argument':'bench-0'},
              'limitations': ['No HTTP connections, server workers, or request queues are exercised',
                              'Stable tracked VM cells do not prove stable Python, SQLite, libc, or OS memory',
                              'RSS is observational only; no threshold or no-leak assertion is applied',
                              'High-water RSS includes imports, compilation, seeding, and all prior phases',
                              'Explicit VM collections change natural execution; durations are diagnostic only',
                              'No explicit Python gc.collect is used; normal Python GC remains enabled',
                              'RSS includes the diagnostic harness and evidence serialization allocations'],
              'batches': [], 'passed': False}
    try:
        with tempfile.TemporaryDirectory(prefix='gopyt-ticket-memory-') as temp:
            root = Path(temp)/'tickets'
            shutil.copytree(args.source,root,ignore=shutil.ignore_patterns('build','.gopyt-state','.gopyt','.gopyt-transaction.lock'))
            _program, artifact, ids = build(str(root))
            vm = VM(artifact,str(root))
            report['artifact_sha256'] = hashlib.sha256((root/'build/out.gobyte').read_bytes()).hexdigest()
            report['after_build'] = memory_sample(vm)
            for index in range(64):
                ident, title = f'bench-{index}', f'Title {index}'
                try:
                    request = Record(vm.type_id_of('tickets.CreateReq'),[ident,title])
                    value = vm.call(ids['tickets.create'],[request])
                    require_reply(vm,value,'created',ident,title)
                finally:
                    vm.heap.release_result()
            report['after_seed_before_gc'] = memory_sample(vm)
            vm.heap.collect()
            report['seeded_post_gc_baseline'] = memory_sample(vm)
            maximum_cells = report['seeded_post_gc_baseline']['vm_heap_cells'] + args.heap_headroom
            report['declared_maximum_post_gc_cells'] = maximum_cells
            for batch in range(args.batches):
                started = time.perf_counter()
                for _ in range(args.calls_per_batch):
                    try:
                        value = vm.call(ids['tickets.retrieve'],['bench-0'])
                        require_reply(vm,value,'ok','bench-0','Title 0')
                    finally:
                        vm.heap.release_result()
                elapsed = time.perf_counter()-started
                row = {'batch':batch+1,'verified_calls':args.calls_per_batch,
                       'cumulative_verified_calls':(batch+1)*args.calls_per_batch,
                       'diagnostic_batch_seconds':elapsed,'before_gc':memory_sample(vm)}
                vm.heap.collect()
                row['after_gc'] = memory_sample(vm)
                row['tracked_heap_within_declared_bound'] = row['after_gc']['vm_heap_cells'] <= maximum_cells
                report['batches'].append(row)
                # Persist each completed batch so interruptions retain evidence.
                (args.output/'memory-summary.json').write_text(json.dumps(report,indent=2)+'\n')
                if not row['tracked_heap_within_declared_bound']:
                    raise AssertionError(f"post-GC VM cells {row['after_gc']['vm_heap_cells']} exceed predeclared bound {maximum_cells}")
                print(f"batch {batch+1}: verified={args.calls_per_batch} post_gc_cells={row['after_gc']['vm_heap_cells']} rss_bytes={row['after_gc']['rss_bytes']}",flush=True)
            # Check all seeded records after the final explicit collection.
            for index in range(64):
                try:
                    value = vm.call(ids['tickets.retrieve'],[f'bench-{index}'])
                    require_reply(vm,value,'ok',f'bench-{index}',f'Title {index}')
                finally:
                    vm.heap.release_result()
            vm.heap.collect()
            report['final_integrity_checked_records'] = 64
            report['final_post_gc'] = memory_sample(vm)
            if report['final_post_gc']['vm_heap_cells'] > maximum_cells:
                raise AssertionError('final integrity reads exceeded the declared post-GC heap bound')
            if (fingerprint(args.source)!=source_hashes or fingerprint(ROOT/'gopyt')!=compiler_hashes
                    or {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in helpers}!=helper_hashes):
                raise AssertionError('source/compiler/probe changed during diagnostic')
            report['artifacts_unchanged'] = True
            report['passed'] = True
    except BaseException as error:
        report['error'] = repr(error)
        raise
    finally:
        (args.output/'memory-summary.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__ == '__main__':
    main()
