#!/usr/bin/env python3
"""Synthetic snapshot-size diagnostic; this does not measure HTTP throughput."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import resource
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gopyt.storage import Store, DATABASE, DIRECTORY  # noqa: E402


def hashes():
    paths = [p for p in (ROOT / 'gopyt').glob('*.py') if not p.name.startswith('test_')]
    paths.append(Path(__file__).resolve())
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def rss():
    try:
        return int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    except (OSError, ValueError, IndexError):
        return None


def high_water_rss():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == 'darwin' else value * 1024) if sys.platform in ('linux', 'darwin') else None


def environment():
    result = {'python': sys.version, 'executable': sys.executable, 'platform': platform.platform(),
              'sqlite_version': sqlite3.sqlite_version,
              'storage_parent': os.path.realpath(tempfile.gettempdir()),
              'cpu_count': os.cpu_count(), 'clock': vars(time.get_clock_info('perf_counter')),
              'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'load_average': list(os.getloadavg()) if hasattr(os, 'getloadavg') else None,
              'cpu_affinity': sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None}
    for name, path in [('cpuinfo', '/proc/cpuinfo'), ('meminfo', '/proc/meminfo'),
                       ('cgroup_cpu_max', '/sys/fs/cgroup/cpu.max'),
                       ('filesystem_mounts', '/proc/self/mountinfo')]:
        try:
            result[name] = Path(path).read_text()
        except OSError:
            result[name] = None
    return result


def summary(rows):
    values = [row['wall_ns'] / 1e6 for row in rows]
    return {'operations': len(rows), 'median_ms': statistics.median(values),
            'min_ms': min(values), 'max_ms': max(values),
            'total_wall_seconds': sum(values) / 1000,
            'total_process_cpu_seconds': sum(row['cpu_ns'] for row in rows) / 1e9}


def worker(args):
    # A fresh process and disposable store for each independent repetition.
    with tempfile.TemporaryDirectory(prefix='gopyt-storage-bench-') as temporary:
        root = os.path.realpath(temporary)
        store = Store(root)
        size = round(args.size_mib * 1024 * 1024)
        if size:
            store.put('synthetic-padding', 'x' * size)
        store.put('target', '0')
        database = Path(root, DIRECTORY, DATABASE)
        initial_bytes = database.stat().st_size
        for _ in range(args.warmup):
            if store.get('target') != '0':
                raise AssertionError('warmup read failed')
        gc.collect()
        samples = []
        stop = threading.Event()
        started = time.perf_counter()
        baseline_rss = rss()
        baseline_peak = high_water_rss()

        def sample():
            while not stop.wait(args.sample_interval):
                samples.append({'elapsed_seconds': time.perf_counter() - started, 'rss_bytes': rss()})

        monitor = threading.Thread(target=sample, daemon=True)
        monitor.start()
        operations = {'read': [], 'compare_exchange': []}
        try:
            for operation, count in [('read', args.reads), ('compare_exchange', args.writes)]:
                for index in range(count):
                    expected, replacement = str(index), str(index + 1)
                    cpu_start = time.process_time_ns()
                    wall_start = time.perf_counter_ns()
                    if operation == 'read':
                        result = store.get('target')
                    else:
                        result = store.compare_exchange('target', expected, replacement)
                    elapsed = time.perf_counter_ns() - wall_start
                    cpu_elapsed = time.process_time_ns() - cpu_start
                    correct = result == '0' if operation == 'read' else result is True
                    operations[operation].append({'index': index, 'wall_ns': elapsed,
                                                  'cpu_ns': cpu_elapsed, 'correct': correct})
                    if not correct:
                        raise AssertionError(f'{operation} returned incorrect result: {result!r}')
            final_rss = rss()
            measured_peak = high_water_rss()
        finally:
            stop.set()
            monitor.join()
        # Final assertions are outside timed operations and memory sampling.
        if Store(root).get('target') != str(args.writes):
            raise AssertionError('persisted target mismatch')
        if size and Store(root).get('synthetic-padding') != 'x' * size:
            raise AssertionError('padding corrupted')
        observed = [v for v in [baseline_rss, final_rss] + [s['rss_bytes'] for s in samples] if v is not None]
        return {'pid': os.getpid(), 'padding_bytes': size, 'database_bytes_before': initial_bytes,
                'database_bytes_after': database.stat().st_size,
                'filesystem_block_size': os.statvfs(root).f_bsize,
                'operations': operations, 'summary': {name: summary(rows) for name, rows in operations.items()},
                'memory': {'baseline_rss_bytes': baseline_rss, 'final_rss_bytes': final_rss,
                           'sampled_window_peak_rss_bytes': max(observed, default=None),
                           'lifetime_peak_before_measurement_bytes': baseline_peak,
                           'lifetime_peak_through_measurement_bytes': measured_peak,
                           'samples': samples},
                'correct': True}


def parse(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--sizes-mib', nargs='+', type=float, default=[0, 1, 8, 32])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--reads', type=int, default=25)
    parser.add_argument('--writes', type=int, default=5)
    parser.add_argument('--warmup', type=int, default=3)
    parser.add_argument('--sample-interval', type=float, default=.005)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--size-mib', type=float, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    sizes = [args.size_mib] if args.worker else args.sizes_mib
    if any(value is None or not math.isfinite(value) or not 0 <= value <= 32 for value in sizes):
        parser.error('sizes must be finite numbers from 0 through 32 MiB')
    if min(args.repeats, args.reads, args.writes) < 1 or args.warmup < 0:
        parser.error('repeats, reads and writes must be positive; warmup may be zero')
    if not math.isfinite(args.sample_interval) or args.sample_interval <= 0:
        parser.error('sample interval must be finite and positive')
    if not args.worker and args.output is None:
        parser.error('--output is required')
    return args


def main(argv=None):
    args = parse(argv)
    if args.worker:
        print(json.dumps(worker(args)))
        return
    args.output.mkdir(parents=True, exist_ok=True)
    frozen = hashes()
    report = {'kind': 'synthetic-storage-snapshot-size-diagnostic', 'environment': environment(),
              'frozen_sha256': frozen,
              'configuration': {key: str(value) if isinstance(value, Path) else value
                                for key, value in vars(args).items() if key not in ('worker', 'size_mib')},
              'limitations': [
                  'Synthetic small-key operations over padded databases; not application or HTTP throughput.',
                  'Fresh subprocess and freshly seeded database per repetition; OS page cache is not flushed.',
                  'Seeding, warmup and final state verification are excluded from operation timings.',
                  'Each successful timed CAS includes whole-snapshot serialization, file fsync, rename and directory fsync.',
                  'Reads precede CAS writes within a trial. The sampling thread shares the measured process.',
                  'Sampled RSS may miss short peaks; lifetime RSS high-water includes untimed seeding and warmup.',
                  'RSS sampling is Linux-only; lifetime peak RSS supports Linux and macOS.',
                  'Process CPU includes sampling overhead. Trials run sequentially on a potentially shared host.',
                  'Three repetitions and small write counts are exploratory evidence, not scalability certification.'],
              'trials': [], 'measurement_complete': False}
    try:
        scenarios = [(repeat + 1, size) for repeat in range(args.repeats) for size in args.sizes_mib]
        random.Random(20260905).shuffle(scenarios)
        for index, (repeat, size) in enumerate(scenarios):
            if hashes() != frozen:
                raise AssertionError('source changed during measurements')
            command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--size-mib', str(size),
                       '--reads', str(args.reads), '--writes', str(args.writes), '--warmup', str(args.warmup),
                       '--sample-interval', str(args.sample_interval)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=300,
                                    env=dict(os.environ, PYTHONHASHSEED='0'))
            if result.returncode:
                raise AssertionError(f'worker exited {result.returncode}: {result.stderr}')
            trial = json.loads(result.stdout)
            trial.update(repetition=repeat, size_mib=size, execution_index=index, command=command)
            report['trials'].append(trial)
            (args.output / f'trial-{index:02d}.json').write_text(json.dumps(trial, indent=2) + '\n')
            if hashes() != frozen:
                raise AssertionError('source changed during measurements')
        report['summary'] = []
        for size in sorted(set(args.sizes_mib)):
            selected = [trial for trial in report['trials'] if trial['size_mib'] == size]
            for operation in ('read', 'compare_exchange'):
                medians = [trial['summary'][operation]['median_ms'] for trial in selected]
                report['summary'].append({'size_mib': size, 'operation': operation,
                                          'trial_medians_ms': medians,
                                          'median_of_trial_medians_ms': statistics.median(medians),
                                          'min_trial_median_ms': min(medians), 'max_trial_median_ms': max(medians)})
        report['measurement_complete'] = True
        report['artifacts_unchanged'] = True
        print(json.dumps(report['summary'], indent=2))
    except BaseException as error:
        report['error'] = repr(error)
        raise
    finally:
        (args.output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
