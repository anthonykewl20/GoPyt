#!/usr/bin/env python3
"""Diagnostic cProfile attribution of ticket handlers invoked directly in the VM.

No HTTP traffic or server workers are involved. Profile times include profiler
instrumentation overhead and must not be reported as application benchmarks.
Run separately, after other measurement jobs have finished.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
from pathlib import Path
import pstats
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gopyt.cli import build
from gopyt.values import Record
from gopyt.vm import VM
from ticket_probe import environment, fingerprint


def require_reply(vm, value, outcome, ident, title):
    expected = [outcome, ident, title, 'open', 1]
    if (not isinstance(value, Record) or value.type_id != vm.type_id_of('tickets.Reply')
            or value.fields != expected or type(value.fields[-1]) is not int):
        raise AssertionError(f'incorrect reply: {value!r}; expected {expected!r}')


def rows(stats, sort_key, limit):
    result = []
    for (file, line, name), (primitive, calls, own, cumulative, callers) in stats.stats.items():
        result.append({'file': file, 'line': line, 'function': name,
                       'primitive_calls': primitive, 'total_calls': calls,
                       'self_seconds': own, 'cumulative_seconds': cumulative})
    result.sort(key=lambda row: row[sort_key], reverse=True)
    return result[:limit]


def run_profile(vm, ids, target, iterations, controls, output, top):
    def invoke():
        return vm.call(ids[target], ['bench-0'])

    def validate(value):
        if target == 'tickets.retrieve':
            require_reply(vm, value, 'ok', 'bench-0', 'Title 0')
        elif value is not True:
            raise AssertionError(f'valid_id returned {value!r} for bench-0')

    control_seconds = []
    for _ in range(controls):
        try:
            begin = time.perf_counter()
            value = invoke()
            control_seconds.append(time.perf_counter() - begin)
            validate(value)
        finally:
            vm.heap.release_result()
    profile = cProfile.Profile()
    begin = time.perf_counter()
    for _ in range(iterations):
        try:
            # Only VM invocation is profiled. Assertions and result-root release
            # run outside the profiler; all calls still validate exact results.
            profile.enable()
            try:
                value = invoke()
            finally:
                profile.disable()
            validate(value)
        finally:
            vm.heap.release_result()
    wall = time.perf_counter() - begin
    filename = target.replace('.', '-') + '.pstats'
    profile.dump_stats(str(output / filename))
    stats = pstats.Stats(profile)
    return {'target': target, 'argument': 'bench-0', 'profiled_iterations': iterations,
            'diagnostic_control_call_seconds': control_seconds,
            'profiled_loop_wall_seconds': wall, 'instrumented_self_seconds_total': stats.total_tt,
            'total_calls': stats.total_calls, 'primitive_calls': stats.prim_calls,
            'pstats_file': filename, 'pstats_sha256': hashlib.sha256((output/filename).read_bytes()).hexdigest(),
            'top_cumulative': rows(stats, 'cumulative_seconds', top),
            'top_self': rows(stats, 'self_seconds', top), 'exact_results_passed': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source', default=ROOT/'examples/tickets', type=Path)
    parser.add_argument('--iterations', default=30, type=int)
    parser.add_argument('--control-iterations', default=3, type=int)
    parser.add_argument('--seed-records', default=64, type=int)
    parser.add_argument('--top', default=40, type=int)
    parser.add_argument('--targets', nargs='+', default=['tickets.retrieve', 'tickets.valid_id'],
                        choices=['tickets.retrieve', 'tickets.valid_id'])
    args = parser.parse_args()
    if args.iterations < 1 or args.control_iterations < 0 or args.seed_records < 1 or args.top < 1:
        parser.error('iterations, seed-records, and top must be positive; controls may be zero')
    args.output.mkdir(parents=True, exist_ok=True)
    frozen = {'compiler': fingerprint(ROOT/'gopyt'), 'app': fingerprint(args.source),
              'profiler_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'probe_helpers_sha256': hashlib.sha256((ROOT/'tools/ticket_probe.py').read_bytes()).hexdigest()}
    report = {'purpose': 'diagnostic attribution, not a throughput or latency benchmark',
              'environment': environment(), 'frozen_sha256': frozen, 'profiles': [], 'passed': False,
              'scope': 'Direct VM calls only; excludes HTTP request parsing, HTTP response serialization, sockets, and server worker scheduling.',
              'limitations': ['cProfile instrumentation changes runtime and relative cost; corroborate before optimization',
                              'control durations are a small diagnostic sample, not a benchmark comparison',
                              'results include handler validation, storage access, VM dispatch and runtime bookkeeping',
                              'build and seeding are outside profiling; profile targets run sequentially in one VM'],
              'seed_records': args.seed_records}
    try:
        with tempfile.TemporaryDirectory(prefix='gopyt-app-profile-') as temp:
            root = Path(temp)/'tickets'
            shutil.copytree(args.source, root, ignore=shutil.ignore_patterns('build', '.gopyt-state', '.gopyt', '.gopyt-transaction.lock'))
            _program, artifact, ids = build(str(root))
            vm = VM(artifact, str(root))
            report['artifact_sha256'] = hashlib.sha256((root/'build/out.gobyte').read_bytes()).hexdigest()
            for index in range(args.seed_records):
                ident, title = f'bench-{index}', f'Title {index}'
                try:
                    request = Record(vm.type_id_of('tickets.CreateReq'), [ident, title])
                    value = vm.call(ids['tickets.create'], [request])
                    require_reply(vm, value, 'created', ident, title)
                finally:
                    vm.heap.release_result()
            for target in args.targets:
                result = run_profile(vm, ids, target, args.iterations, args.control_iterations, args.output, args.top)
                report['profiles'].append(result)
                print(f'{target}: {args.iterations} profiled calls, exact results passed', flush=True)
            current = {'compiler': fingerprint(ROOT/'gopyt'), 'app': fingerprint(args.source),
                       'profiler_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       'probe_helpers_sha256': hashlib.sha256((ROOT/'tools/ticket_probe.py').read_bytes()).hexdigest()}
            if current != frozen:
                raise AssertionError('compiler, app, or profiling harness changed during profiling')
            report['passed'] = True
            report['artifacts_unchanged'] = True
    except BaseException as error:
        report['error'] = repr(error)
        raise
    finally:
        (args.output/'profile-summary.json').write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
