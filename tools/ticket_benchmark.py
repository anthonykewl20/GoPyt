#!/usr/bin/env python3
"""Repeatable ticket-app HTTP measurements, with external correctness gating."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import random
import statistics
import threading
import time

from ticket_probe import ROOT, Client, Server, acceptance, envelope, environment, fingerprint


def percentile(values, percent):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percent / 100 * len(ordered)) - 1)]


def process_sample(pid):
    """Linux resident memory and user+system CPU; no heap-cell substitutions."""
    try:
        data = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return {'rss_bytes': int(data[21]) * os.sysconf('SC_PAGE_SIZE'),
                'cpu_seconds': (int(data[11]) + int(data[12])) / os.sysconf('SC_CLK_TCK'),
                'threads': int(data[17])}
    except (OSError, ValueError, IndexError):
        return {'rss_bytes': None, 'cpu_seconds': None, 'threads': None}


def latency_summary(records, field):
    values = [r[field] * 1000 for r in records if r.get(field) is not None and not r.get('dropped')]
    return {f'p{p}_ms': percentile(values, p) for p in [50, 95, 99]} | {'max_ms': max(values, default=None)}


def trial(source, output, name, mode, concurrency, duration, rate, warmup, write=False, max_writes=300):
    records, samples = [], []
    lock = threading.Lock()
    with Server(source) as server:
        check_seconds = server.command('check')
        start_seconds = server.start()
        setup = Client(server.port)
        for index in range(64):
            code, value = setup.request('POST', '/tickets', {'id': f'bench-{index}', 'title': f'Title {index}'})
            assert code == 200 and value == envelope('created', f'bench-{index}', f'Title {index}', 'open', 1), (code, value)
        until = time.perf_counter() + warmup
        while time.perf_counter() < until:
            code, value = setup.request('GET', '/tickets/bench-0')
            assert code == 200 and value == envelope('ok', 'bench-0', 'Title 0', 'open', 1)
        setup.close()
        baseline = process_sample(server.proc.pid)
        generator_baseline = process_sample(os.getpid())
        stop_sample = threading.Event()
        def sampler():
            while not stop_sample.wait(.1):
                samples.append({'elapsed': time.perf_counter() - started,
                                'server': process_sample(server.proc.pid), 'generator': process_sample(os.getpid())})
        # Fixed source + seed + sequence produce identical request contents per scenario.
        rng = random.Random(20260905)
        choices = [rng.randrange(64) for _ in range(4096)]
        started = time.perf_counter()
        deadline = started + duration
        monitor = threading.Thread(target=sampler, daemon=True)
        monitor.start()
        counter = 0

        def perform(client, index, scheduled):
            begin = time.perf_counter()
            result = {'index': index, 'scheduled': scheduled - started, 'start': begin - started,
                      'scheduler_delay': begin - scheduled, 'dropped': False}
            try:
                if write:
                    ident = f'write-{index}'
                    code, value = client.request('POST', '/tickets', {'id': ident, 'title': 'Measured write'})
                    expected = envelope('created', ident, 'Measured write', 'open', 1)
                else:
                    which = choices[index % len(choices)]
                    code, value = client.request('GET', f'/tickets/bench-{which}')
                    expected = envelope('ok', f'bench-{which}', f'Title {which}', 'open', 1)
                result.update(status=code, correct=code == 200 and value == expected and isinstance(value, dict) and type(value.get('version')) is int)
                if not result['correct']:
                    result['response'] = value
            except Exception as error:
                result.update(status=None, correct=False, error=repr(error))
                client.close()
            done = time.perf_counter()
            result.update(end=done - started, service_seconds=done - begin, offered_seconds=done - scheduled)
            with lock:
                records.append(result)

        if mode == 'closed':
            gate = threading.Barrier(concurrency)
            def closed_worker(_):
                nonlocal counter
                client = Client(server.port)
                try:
                    gate.wait(timeout=30)
                    while time.perf_counter() < deadline:
                        with lock:
                            if write and counter >= max_writes:
                                break
                            index = counter
                            counter += 1
                        perform(client, index, time.perf_counter())
                finally:
                    client.close()
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                list(pool.map(closed_worker, range(concurrency)))
            planned = counter
        else:
            jobs = queue.Queue(maxsize=concurrency * 2)
            def open_worker():
                client = Client(server.port)
                try:
                    while True:
                        item = jobs.get()
                        try:
                            if item is None:
                                return
                            perform(client, *item)
                        finally:
                            jobs.task_done()
                finally:
                    client.close()
            workers = [threading.Thread(target=open_worker) for _ in range(concurrency)]
            for worker in workers:
                worker.start()
            planned = math.ceil(duration * rate)
            for index in range(planned):
                scheduled = started + index / rate
                delay = scheduled - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                reason = None
                if time.perf_counter() >= deadline:
                    reason = 'generator_late'
                else:
                    try:
                        jobs.put_nowait((index, scheduled))
                    except queue.Full:
                        reason = 'queue_full'
                if reason:
                    with lock:
                        records.append({'index': index, 'scheduled': scheduled-started, 'dropped': True,
                                        'correct': False, 'drop_reason': reason,
                                        'scheduler_delay': time.perf_counter() - scheduled})
            jobs.join()
            for _ in workers:
                jobs.put(None)
            for worker in workers:
                worker.join()
        elapsed = time.perf_counter() - started
        stop_sample.set()
        monitor.join()
        final = process_sample(server.proc.pid)
        generator_final = process_sample(os.getpid())
        records.sort(key=lambda r: r['index'])
        (output / (name + '.jsonl')).write_text(''.join(json.dumps(r)+'\n' for r in records))
        (output / (name + '.json')).write_text(json.dumps({'name': name, 'measurement_complete': False,
            'raw_records_file': name+'.jsonl', 'planned': planned, 'recorded': len(records)}, indent=2)+'\n')
        assert len(records) == planned
        assert planned > 0 and any(not r['dropped'] for r in records), 'trial completed zero requests'
        assert fingerprint(server.root) == server.source_hashes, 'trial app changed during measurement'
        drops = sum(r['dropped'] for r in records)
        successes = sum(r.get('correct', False) for r in records)
        result = {'name': name, 'mode': mode, 'concurrency': concurrency, 'workload': 'create' if write else 'read',
                  'offered_rate': rate if mode == 'open' else None, 'configured_seconds': duration,
                  'elapsed_including_drain_seconds': elapsed, 'warmup_seconds': warmup,
                  'startup_including_compile_seconds': start_seconds, 'separate_check_seconds': check_seconds,
                  'source_sha256': server.source_hashes,
                  'artifact_sha256': hashlib.sha256((server.root / 'build/out.gobyte').read_bytes()).hexdigest(),
                  'planned': planned, 'dispatched': planned-drops,
                  'responses_received': sum(r.get('status') is not None for r in records),
                  'successful': successes, 'dropped': drops, 'integrity_passed': False,
                  'errors': planned-drops-successes, 'successes_per_second_including_drain': successes/elapsed,
                  'completed_by_deadline': sum(r.get('end', math.inf) <= duration for r in records),
                  'successful_by_deadline': sum(r.get('correct', False) and r.get('end', math.inf) <= duration for r in records),
                  'request_latency': latency_summary(records, 'service_seconds'),
                  'offered_latency': latency_summary(records, 'offered_seconds'),
                  'scheduler_delay_ms': {f'p{p}': percentile([r['scheduler_delay']*1000 for r in records], p) for p in [50,95,99]},
                  'baseline': baseline, 'final': final, 'generator_baseline': generator_baseline, 'generator_final': generator_final,
                  'peak_sampled_rss_bytes': max([s['server']['rss_bytes'] or 0 for s in samples]+[baseline['rss_bytes'] or 0, final['rss_bytes'] or 0]) or None,
                  'server_cpu_seconds': (final['cpu_seconds']-baseline['cpu_seconds']) if final['cpu_seconds'] is not None else None,
                  'generator_cpu_seconds': (generator_final['cpu_seconds']-generator_baseline['cpu_seconds']) if generator_final['cpu_seconds'] is not None else None,
                  'resource_samples': samples, 'raw_records_file': name+'.jsonl'}
        (output / (name + '.jsonl')).write_text(''.join(json.dumps(r)+'\n' for r in records))
        (output / (name + '.json')).write_text(json.dumps(result, indent=2)+'\n')
        try:
            # End-of-trial integrity check is outside the measurement window.
            verify = Client(server.port)
            try:
                for index in range(64):
                    assert verify.request('GET', f'/tickets/bench-{index}') == (200, envelope('ok', f'bench-{index}', f'Title {index}', 'open', 1))
                if write:
                    for row in records:
                        if row.get('correct'):
                            ident = f"write-{row['index']}"
                            assert verify.request('GET', '/tickets/' + ident) == (200, envelope('ok', ident, 'Measured write', 'open', 1))
            finally:
                verify.close()
            result['integrity_passed'] = True
        except BaseException as error:
            result['integrity_error'] = repr(error)
            raise
        finally:
            (output / (name + '.json')).write_text(json.dumps(result, indent=2)+'\n')
        print(f'{name}: requests={planned} successful={successes} errors={result["errors"]} drops={drops}', flush=True)
        return result


def summarize(trials):
    groups = {}
    for item in trials:
        key = f"{item['workload']}-{item['mode']}-{item['concurrency']}-{item['offered_rate']}"
        groups.setdefault(key, []).append(item)
    result = {}
    for key, group in groups.items():
        rates = [x['successes_per_second_including_drain'] for x in group]
        result[key] = {'trials': len(group), 'throughput_each': rates,
                       'mean_successes_per_second': statistics.mean(rates),
                       'sample_stdev': statistics.stdev(rates) if len(rates)>1 else None,
                       'min': min(rates), 'max': max(rates),
                       'error_counts': [x['errors'] for x in group], 'drop_counts': [x['dropped'] for x in group],
                       'request_p99_ms_each': [x['request_latency']['p99_ms'] for x in group],
                       'offered_p99_ms_each': [x['offered_latency']['p99_ms'] for x in group]}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source', type=Path, default=ROOT/'examples/tickets')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--seconds', type=float, default=3)
    parser.add_argument('--warmup', type=float, default=1)
    parser.add_argument('--soak-seconds', type=float, default=60)
    parser.add_argument('--clients', type=int, nargs='+', default=[1,4,16,64])
    parser.add_argument('--rates', type=float, nargs='+', default=[25,100,400])
    parser.add_argument('--open-workers', type=int, default=16)
    args = parser.parse_args()
    if not all(math.isfinite(n) for n in [args.seconds,args.warmup,args.soak_seconds,*args.rates]) or args.repeats < 1 or args.seconds <= 0 or args.warmup < 0 or args.soak_seconds < 0 or args.open_workers < 1 or any(c < 1 for c in args.clients) or any(r <= 0 for r in args.rates):
        parser.error('durations/rates/clients/repeats must be positive (warmup and soak may be zero)')
    args.output.mkdir(parents=True, exist_ok=True)
    frozen = {'compiler': fingerprint(ROOT/'gopyt'), 'app': fingerprint(args.source),
              'harness': {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'tools').glob('ticket_*.py')}}
    def verify_frozen():
        current = {'compiler': fingerprint(ROOT/'gopyt'), 'app': fingerprint(args.source),
                   'harness': {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'tools').glob('ticket_*.py')}}
        if current != frozen:
            raise AssertionError('compiler/app/harness changed during benchmark; discard mixed-artifact measurements')
    acceptance(args.output/'acceptance', args.source)
    verify_frozen()
    report = {'environment': environment(), 'frozen_sha256': frozen, 'configuration': {k:str(v) if isinstance(v, Path) else v for k,v in vars(args).items()},
              'limitations': ['shared-host localhost exploratory benchmark; no cross-language speed claim',
                              'closed-loop loads are response-paced; inspect offered-time open-loop results for overload',
                              'open-loop drops excluded from percentiles and always reported separately',
                              'RSS sampled at 100ms; peaks between samples may be missed',
                              'fresh processes do not flush OS filesystem cache; startup includes imports and compilation',
                              'generator and server share host; generator CPU and scheduling delays are reported'],
              'trials': []}
    try:
        scenarios = [('closed', c, None, False) for c in args.clients] + [('open', args.open_workers, r, False) for r in args.rates] + [('closed',4,None,True)]
        order = random.Random(912)
        for repeat in range(args.repeats):
            order.shuffle(scenarios)
            for mode, clients, rate, write in scenarios:
                verify_frozen()
                name = f'r{repeat+1}-{"write" if write else "read"}-{mode}-c{clients}' + (f'-rate{rate:g}' if rate else '')
                report['trials'].append(trial(args.source,args.output,name,mode,clients,args.seconds,rate,args.warmup,write))
        if args.soak_seconds:
            report['soak'] = trial(args.source,args.output,'soak-read-closed-c16','closed',16,args.soak_seconds,None,args.warmup)
        verify_frozen()
        report['artifacts_unchanged'] = True
        report['summary'] = summarize(report['trials'])
        report['measurement_complete'] = True
        report['all_responses_correct'] = all(t['errors']==0 for t in report['trials']) and report.get('soak',{}).get('errors',0)==0
    except BaseException as error:
        report['measurement_complete'] = False
        report['error'] = repr(error)
        raise
    finally:
        (args.output/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    if not report['all_responses_correct']:
        raise SystemExit('Measured response errors; inspect raw records before interpreting performance')


if __name__ == '__main__':
    main()
