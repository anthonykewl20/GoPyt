#!/usr/bin/env python3
"""Alternating-order runtime comparison using the frozen ticket HTTP driver.

Each worker imports the driver and runtime from its declared source snapshot.
Timing jobs are sequential. Exact-response errors fail the campaign; performance
numbers are observations, never pass thresholds.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import subprocess
import sys


def identities(root):
    paths = list((root / 'gopyt').glob('*.py'))
    paths += list((root / 'examples/tickets').glob('*/tickets.gopyt'))
    paths += [root / 'examples/tickets' / name for name in ('README.md', 'gopyt.toml', 'gopyt.lock')]
    paths += [root / 'tools' / name for name in ('ticket_benchmark.py', 'ticket_probe.py')]
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-root', type=Path)
    parser.add_argument('--candidate-root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--clients', type=int, nargs='+', default=[1, 16, 64])
    parser.add_argument('--seconds', type=float, default=5)
    parser.add_argument('--warmup', type=float, default=2)
    parser.add_argument('--soak-seconds', type=float, default=600)
    parser.add_argument('--worker-root', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--name', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.repeats < 1 or not args.clients or min(args.clients) < 1:
        parser.error('positive repeats and client counts required')
    if (not all(math.isfinite(v) for v in (args.seconds, args.warmup, args.soak_seconds))
            or args.seconds <= 0 or args.warmup < 0 or args.soak_seconds < 0):
        parser.error('finite positive seconds and nonnegative warmup/soak required')
    args.output.mkdir(parents=True, exist_ok=True)
    if args.worker_root:
        root = args.worker_root.resolve()
        sys.path[:0] = [str(root / 'tools'), str(root)]
        from ticket_benchmark import trial
        from ticket_probe import environment
        frozen = identities(root)
        result = trial(root / 'examples/tickets', args.output, args.name,
                       'closed', args.clients[0], args.seconds, None, args.warmup)
        metadata = {'trial': result, 'environment': environment(), 'source_sha256': frozen,
                    'source_unchanged': identities(root) == frozen}
        (args.output / (args.name + '-metadata.json')).write_text(json.dumps(metadata, indent=2) + '\n')
        if result['errors'] or not metadata['source_unchanged']:
            raise AssertionError('incorrect replies or changed runtime during trial')
        return
    if args.baseline_root is None or args.candidate_root is None:
        parser.error('baseline-root and candidate-root required')
    if any(args.output.iterdir()):
        parser.error('output must be empty to preserve earlier evidence')
    runner_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    roots = {'baseline': args.baseline_root.resolve(), 'candidate': args.candidate_root.resolve()}
    frozen = {label: identities(root) for label, root in roots.items()}
    for name, digest in frozen['baseline'].items():
        if name.startswith(('examples/', 'tools/')) and frozen['candidate'].get(name) != digest:
            raise AssertionError('app or HTTP measurement driver differs: ' + name)
    report = {'configuration': {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
              'source_sha256': frozen, 'runner_sha256': runner_hash,
              'limitations': ['Shared-host localhost experiment; no CPU or filesystem cache isolation',
                              'Five-second closed-loop windows are response-paced, not sustainable offered-rate capacity',
                              'Alternating pair order reduces but does not eliminate host/time-order confounding',
                              'Warmup uses one client; measured high concurrency includes worker ramp-up',
                              'Soak is final-runtime only, not a paired equal-work memory comparison'],
              'trials': [], 'complete': False}

    def run(label, name, clients, seconds):
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != runner_hash:
            raise AssertionError('comparison runner changed during campaign')
        if any(identities(root) != frozen[tag] for tag, root in roots.items()):
            raise AssertionError('runtime source changed during comparison')
        command = [sys.executable, str(Path(__file__).resolve()), '--worker-root', str(roots[label]),
                   '--name', name, '--output', str(args.output.resolve()), '--clients', str(clients),
                   '--seconds', str(seconds), '--warmup', str(args.warmup)]
        subprocess.run(command, check=True, timeout=seconds + 180)
        data = json.loads((args.output / (name + '-metadata.json')).read_text())
        if data['source_sha256'] != frozen[label] or not data['source_unchanged']:
            raise AssertionError('worker source identity differs')
        print(name + ': ' + str(round(data['trial']['successes_per_second_including_drain'], 2)) + '/s', flush=True)
        return {'variant': label, 'name': name, **data['trial']}

    try:
        order = random.Random(20260905)
        for repeat in range(args.repeats):
            clients = list(args.clients)
            order.shuffle(clients)
            for count in clients:
                for label in (['baseline', 'candidate'] if repeat % 2 == 0 else ['candidate', 'baseline']):
                    name = f'r{repeat + 1}-{label}-c{count}'
                    report['trials'].append(run(label, name, count, args.seconds))
        if args.soak_seconds:
            report['soak'] = run('candidate', 'candidate-soak-c16', 16, args.soak_seconds)
        groups = {}
        for row in report['trials']:
            groups.setdefault(f"{row['variant']}-c{row['concurrency']}", []).append(row['successes_per_second_including_drain'])
        report['summary'] = {key: {'throughput_each': values, 'mean': statistics.mean(values),
                                    'sample_stdev': statistics.stdev(values) if len(values) > 1 else None}
                             for key, values in groups.items()}
        if any(identities(root) != frozen[tag] for tag, root in roots.items()):
            raise AssertionError('runtime source changed during comparison')
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != runner_hash:
            raise AssertionError('comparison runner changed during campaign')
        report['complete'] = True
    except BaseException as error:
        report['error'] = repr(error)
        raise
    finally:
        (args.output / 'comparison-summary.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
