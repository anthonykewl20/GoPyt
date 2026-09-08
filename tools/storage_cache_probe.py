#!/usr/bin/env python3
"""Compare repeated-key reads with fresh-Store reads; verify process freshness."""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import platform
import random
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time

HARNESS = Path(__file__).resolve()


def identities(runtime_root):
    return {
        'runtime_sha256': {
            str(path.relative_to(runtime_root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((runtime_root / 'gopyt').glob('*.py'))
            if not path.name.startswith('test_')
        },
        'harness_sha256': hashlib.sha256(HARNESS.read_bytes()).hexdigest(),
    }


def environment():
    result = {
        'python': sys.version, 'executable': sys.executable,
        'platform': platform.platform(), 'sqlite_version': sqlite3.sqlite_version,
        'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'cpu_count': os.cpu_count(),
        'cpu_affinity': sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None,
        'load_average': list(os.getloadavg()) if hasattr(os, 'getloadavg') else None,
        'clock': vars(time.get_clock_info('perf_counter')),
        'process_cpu_clock': vars(time.get_clock_info('process_time')),
        'temporary_parent': os.path.realpath(tempfile.gettempdir()),
    }
    for name, path in [('cpuinfo', '/proc/cpuinfo'), ('meminfo', '/proc/meminfo'),
                       ('cgroup_cpu_max', '/sys/fs/cgroup/cpu.max'),
                       ('filesystem_mounts', '/proc/self/mountinfo')]:
        try:
            result[name] = Path(path).read_text()
        except OSError:
            result[name] = None
    return result


def summarize(rows):
    if not rows:
        return {'operations': 0}
    walls = [row['wall_ns'] / 1e6 for row in rows]
    return {
        'operations': len(rows), 'median_ms': statistics.median(walls),
        'min_ms': min(walls), 'max_ms': max(walls),
        'total_wall_seconds': sum(row['wall_ns'] for row in rows) / 1e9,
        'total_process_cpu_seconds': sum(row['cpu_ns'] for row in rows) / 1e9,
        'correct_operations': sum(row['correct'] for row in rows),
    }


def worker(args):
    before = identities(args.runtime_root)
    report = {
        'pid': os.getpid(), 'environment': environment(), 'identity_before': before,
        'operations': [], 'external_updates': [], 'measurement_complete': False,
        'padding_bytes': round(args.size_mib * 1024 * 1024),
    }
    try:
        # Workers run with -I and load the declared tree explicitly. Never import
        # the checkout's runtime as an incidental consequence of script location.
        sys.path.insert(0, str(args.runtime_root))
        storage = importlib.import_module('gopyt.storage')
        imported = Path(storage.__file__).resolve()
        if imported != args.runtime_root / 'gopyt' / 'storage.py':
            raise AssertionError(f'wrong runtime imported: {imported}')
        report['imported_storage'] = str(imported)
        with tempfile.TemporaryDirectory(prefix='gopyt-cache-probe-') as temporary:
            package_root = os.path.realpath(temporary)
            hot_store = storage.Store(package_root)
            padding = 'x' * report['padding_bytes']
            if padding:
                hot_store.put('synthetic-padding', padding)
            hot_store.put('target', '0')
            database = Path(package_root, storage.DIRECTORY, storage.DATABASE)
            report['database_bytes_before'] = database.stat().st_size
            report['filesystem_block_size'] = os.statvfs(package_root).f_bsize
            for _ in range(args.warmup):
                if hot_store.get('target') != '0':
                    raise AssertionError('hot-store warmup failed')
            # Instance allocation and initialization are excluded from timings.
            cold_stores = [storage.Store(package_root) for _ in range(args.reads)]
            gc.collect()
            start_with_hot = random.Random(args.trial_seed).choice((True, False))
            report['first_pair_hot_first'] = start_with_hot
            report['trial_seed'] = args.trial_seed
            # Alternating order balances each paired observation (odd counts
            # differ by one). The initial order is independently seeded per trial.
            for index, cold_store in enumerate(cold_stores):
                order = [('hot_repeated_key', hot_store), ('cold_fresh_store', cold_store)]
                if (index % 2 == 0) != start_with_hot:
                    order.reverse()
                for condition, store in order:
                    cpu_start = time.process_time_ns()
                    wall_start = time.perf_counter_ns()
                    try:
                        value = store.get('target')
                    except BaseException as error:
                        report['operations'].append({
                            'execution_index': len(report['operations']), 'pair_index': index,
                            'condition': condition, 'wall_ns': time.perf_counter_ns() - wall_start,
                            'cpu_ns': time.process_time_ns() - cpu_start,
                            'correct': False, 'error': repr(error),
                        })
                        raise
                    wall_ns = time.perf_counter_ns() - wall_start
                    cpu_ns = time.process_time_ns() - cpu_start
                    correct = type(value) is str and value == '0'
                    report['operations'].append({
                        'execution_index': len(report['operations']), 'pair_index': index,
                        'condition': condition, 'wall_ns': wall_ns, 'cpu_ns': cpu_ns,
                        'result': value, 'correct': correct,
                    })
                    if not correct:
                        raise AssertionError(f'{condition} returned {value!r}')
            # This is correctness-only: child startup, writes, and freshness
            # reads are excluded from the two read timing series above.
            writer = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import gopyt.storage as storage
assert Path(storage.__file__).resolve() == Path(sys.argv[1], 'gopyt', 'storage.py')
storage.Store(sys.argv[2]).put('target', sys.argv[3])
'''
            for index in range(args.updates):
                expected = str(index + 1)
                command = [sys.executable, '-I', '-c', writer, str(args.runtime_root),
                           package_root, expected]
                child = subprocess.run(command, capture_output=True, text=True, timeout=60)
                update = {'index': index, 'expected': expected, 'returncode': child.returncode,
                          'stdout': child.stdout, 'stderr': child.stderr, 'correct': False}
                report['external_updates'].append(update)
                if child.returncode:
                    raise AssertionError(f'external writer failed: {child.stderr}')
                actual = hot_store.get('target')
                update.update(actual=actual, correct=type(actual) is str and actual == expected)
                if not update['correct']:
                    raise AssertionError(f'cached read is stale: {actual!r} != {expected!r}')
            if storage.Store(package_root).get('target') != str(args.updates):
                raise AssertionError('final persisted target is wrong')
            if padding and storage.Store(package_root).get('synthetic-padding') != padding:
                raise AssertionError('padding changed')
            report['database_bytes_after'] = database.stat().st_size
            report['final_integrity_correct'] = True
        report['summary'] = {
            condition: summarize([row for row in report['operations'] if row['condition'] == condition])
            for condition in ('hot_repeated_key', 'cold_fresh_store')
        }
        report['measurement_complete'] = True
    except BaseException as error:
        report['error'] = repr(error)
    finally:
        report['identity_after'] = identities(args.runtime_root)
        report['artifacts_unchanged'] = report['identity_after'] == before
        if not report['artifacts_unchanged']:
            report['measurement_complete'] = False
            report['error'] = 'runtime or harness changed during worker'
    return report


def parse(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--sizes-mib', nargs='+', type=float, default=[0, 8, 32])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--reads', type=int, default=25)
    parser.add_argument('--updates', type=int, default=5)
    parser.add_argument('--warmup', type=int, default=3)
    parser.add_argument('--seed', type=int, default=20260905)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--size-mib', type=float, help=argparse.SUPPRESS)
    parser.add_argument('--trial-seed', type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    args.runtime_root = args.runtime_root.resolve()
    if not (args.runtime_root / 'gopyt' / 'storage.py').is_file():
        parser.error('--runtime-root must contain gopyt/storage.py')
    sizes = [args.size_mib] if args.worker else args.sizes_mib
    if any(size is None or not math.isfinite(size) or not 0 <= size <= 32 for size in sizes):
        parser.error('sizes must be finite numbers from 0 through 32 MiB')
    if min(args.repeats, args.reads, args.updates, args.warmup) < 1:
        parser.error('repeats, reads, updates and warmup must be positive')
    if not args.worker and args.output is None:
        parser.error('--output is required')
    return args


def main(argv=None):
    args = parse(argv)
    if args.worker:
        report = worker(args)
        print(json.dumps(report, allow_nan=False))
        return 0 if report['measurement_complete'] else 1
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        raise SystemExit('output directory must be empty to preserve prior evidence')
    before = identities(args.runtime_root)
    report = {
        'kind': 'storage-cache-hot-versus-fresh-store-diagnostic',
        'environment': environment(), 'identity_before': before,
        'configuration': {key: str(value) if isinstance(value, Path) else value
                          for key, value in vars(args).items()
                          if key not in ('worker', 'size_mib', 'trial_seed')},
        'limitations': [
            'Synthetic one-key reads over padded SQLite snapshots, not application or HTTP throughput.',
            'Each repetition is a fresh subprocess and newly seeded store; paired conditions use the same persisted data.',
            'Hot means one repeatedly requested key after warmup. On a runtime without a cache this is still a full load.',
            'Cold means a fresh Store object per get, with its construction excluded; OS and filesystem caches are not flushed.',
            'Results do not represent cold disks, large cache working sets, cache churn, or concurrent/mixed workloads.',
            'Seeding, construction, warmup, external writes, freshness checks and final integrity checks are untimed.',
            'Read condition order alternates within each trial; initial order and size/repetition schedule are seeded and recorded.',
            'Per-operation process CPU includes timing instrumentation but excludes external writer CPU.',
            'Trials run sequentially on a potentially shared host; three repetitions are exploratory evidence.',
            'External updates verify cached freshness only; they do not benchmark write latency or failure durability.',
        ],
        'trials': [], 'measurement_complete': False,
    }
    try:
        randomizer = random.Random(args.seed)
        scenarios = [(repeat + 1, size) for repeat in range(args.repeats) for size in args.sizes_mib]
        randomizer.shuffle(scenarios)
        for index, (repetition, size) in enumerate(scenarios):
            if identities(args.runtime_root) != before:
                raise AssertionError('runtime or harness changed before trial')
            trial_seed = randomizer.randrange(2 ** 32)
            command = [sys.executable, '-I', str(HARNESS), '--worker',
                       '--runtime-root', str(args.runtime_root), '--size-mib', str(size),
                       '--reads', str(args.reads), '--updates', str(args.updates),
                       '--warmup', str(args.warmup), '--trial-seed', str(trial_seed)]
            try:
                child = subprocess.run(command, capture_output=True, text=True, timeout=300)
                try:
                    trial = json.loads(child.stdout)
                except json.JSONDecodeError:
                    trial = {'measurement_complete': False, 'error': 'worker did not emit JSON',
                             'stdout': child.stdout}
                trial.update(command=command, returncode=child.returncode, stderr=child.stderr)
            except subprocess.TimeoutExpired as error:
                trial = {'measurement_complete': False, 'error': repr(error), 'command': command}
            trial.update(execution_index=index, repetition=repetition, size_mib=size)
            report['trials'].append(trial)
            (args.output / f'trial-{index:02d}.json').write_text(json.dumps(trial, indent=2, allow_nan=False) + '\n')
            if not trial['measurement_complete'] or trial.get('returncode') != 0:
                raise AssertionError(f'trial {index} failed; retained its partial evidence')
            if trial['identity_before'] != before or trial['identity_after'] != before:
                raise AssertionError('worker runtime or harness identity differs')
        report['summary'] = []
        for size in sorted(set(args.sizes_mib)):
            selected = [trial for trial in report['trials'] if trial['size_mib'] == size]
            for condition in ('hot_repeated_key', 'cold_fresh_store'):
                medians = [trial['summary'][condition]['median_ms'] for trial in selected]
                report['summary'].append({
                    'size_mib': size, 'condition': condition,
                    'trial_medians_ms': medians, 'median_of_trial_medians_ms': statistics.median(medians),
                    'min_trial_median_ms': min(medians), 'max_trial_median_ms': max(medians),
                })
        report['measurement_complete'] = True
    except BaseException as error:
        report['error'] = repr(error)
    finally:
        report['identity_after'] = identities(args.runtime_root)
        report['artifacts_unchanged'] = report['identity_after'] == before
        if not report['artifacts_unchanged']:
            report['measurement_complete'] = False
            report['error'] = 'runtime or harness changed during campaign'
        (args.output / 'summary.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps(report.get('summary', {'error': report.get('error')}), indent=2))
    return 0 if report['measurement_complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
