#!/usr/bin/env python3
"""Recompute retained follow-up measurements without importing their drivers.

This is a separate implementation of the arithmetic and consistency checks,
not independent data collection or cryptographic attestation. Successful HTTP
response bodies are not retained by the driver, so their semantic correctness
cannot be reconstructed from its recorded status/correctness flags.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import statistics


def read(path):
    return json.loads(path.read_text())


def require(condition, explanation):
    if not condition:
        raise AssertionError(explanation)


def equal(actual, expected, label):
    if isinstance(expected, float):
        require(isinstance(actual, (float, int)) and math.isfinite(actual)
                and math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9),
                f'{label}: {actual!r} != {expected!r}')
    else:
        require(actual == expected, f'{label}: {actual!r} != {expected!r}')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_hashes(root, expected):
    require(bool(expected), f'empty source inventory: {root}')
    for relative, expected_hash in expected.items():
        path = root / relative
        require(path.resolve().is_relative_to(root.resolve()), f'escaping inventory path: {relative}')
        equal(digest(path), expected_hash, f'{root.name}/{relative} source hash')


def nearest(values, percentile):
    ordered = sorted(values)
    require(bool(ordered), 'empty latency sample')
    return ordered[(len(ordered) * percentile + 99) // 100 - 1]


def verify_http_resources(trial):
    name = trial['name']
    samples = trial['resource_samples']
    previous_elapsed = -1
    for sample in samples:
        when = sample['elapsed']
        require(type(when) in (int, float) and math.isfinite(when)
                and previous_elapsed <= when and when >= 0,
                name + ': invalid or nonmonotonic resource sample time')
        previous_elapsed = when
    for process, baseline_key, final_key, delta_key in (
            ('server', 'baseline', 'final', 'server_cpu_seconds'),
            ('generator', 'generator_baseline', 'generator_final', 'generator_cpu_seconds')):
        baseline, final = trial[baseline_key], trial[final_key]
        observations = [baseline, *(sample[process] for sample in samples), final]
        for observation in observations:
            for key in ('rss_bytes', 'cpu_seconds', 'threads'):
                value = observation[key]
                require(value is None or (type(value) in (int, float) and math.isfinite(value) and value >= 0),
                        name + ': invalid ' + process + ' ' + key)
                if value is not None and key in ('rss_bytes', 'threads'):
                    require(type(value) is int, name + ': noninteger ' + process + ' ' + key)
        start_cpu, final_cpu = baseline['cpu_seconds'], final['cpu_seconds']
        if start_cpu is None or final_cpu is None:
            equal(trial[delta_key], None, name + ': unavailable ' + process + ' CPU delta')
        else:
            require(final_cpu >= start_cpu, name + ': decreasing ' + process + ' endpoint CPU')
            equal(trial[delta_key], final_cpu - start_cpu, name + ': ' + process + ' CPU delta')
        if process == 'server':
            available_rss = [observation['rss_bytes'] for observation in observations
                             if observation['rss_bytes'] is not None]
            # The frozen producer uses zero to represent unavailable aggregate
            # RSS. Retain that exact representation without claiming a true peak.
            equal(trial['peak_sampled_rss_bytes'], max(available_rss, default=0) or None,
                  name + ': peak sampled RSS')


def verify_http_trial(directory, row):
    name = row['name']
    trial = read(directory / (name + '.json'))
    metadata = read(directory / (name + '-metadata.json'))
    equal(metadata['trial'], trial, name + ' metadata/trial equality')
    for key, value in trial.items():
        equal(row[key], value, name + '/' + key)
    require(trial['integrity_passed'] is True, name + ': final integrity did not pass')
    require(metadata['source_unchanged'] is True, name + ': source changed')
    equal(trial['mode'], 'closed', name + ': expected closed-loop scenario')
    equal(trial['workload'], 'read', name + ': expected read scenario')
    equal(trial['raw_records_file'], name + '.jsonl', name + ': raw record filename')
    records = [json.loads(line) for line in (directory / trial['raw_records_file']).read_text().splitlines()]
    equal(len(records), trial['planned'], name + ': record count')
    equal(sorted(record['index'] for record in records), list(range(len(records))), name + ': unique index coverage')
    require(bool(records), name + ': empty trial')
    for record in records:
        require(record['dropped'] is False, name + ': closed-loop request dropped')
        require(record['correct'] is True and record['status'] == 200, name + ': recorded request failed')
        require(all(math.isfinite(record[key]) for key in
                    ('scheduled', 'start', 'end', 'scheduler_delay', 'service_seconds', 'offered_seconds')),
                name + ': nonfinite request timing')
        require(0 <= record['scheduled'] <= record['start'] <= record['end'], name + ': inconsistent request ordering')
        equal(record['service_seconds'], record['end'] - record['start'], name + ': service duration')
        equal(record['offered_seconds'], record['end'] - record['scheduled'], name + ': offered duration')
        equal(record['scheduler_delay'], record['start'] - record['scheduled'], name + ': scheduler duration')
    for key in ('planned', 'dispatched', 'responses_received', 'successful'):
        equal(trial[key], len(records), name + '/' + key)
    equal(trial['errors'], 0, name + ': errors')
    equal(trial['dropped'], 0, name + ': drops')
    elapsed = trial['elapsed_including_drain_seconds']
    require(elapsed >= max(record['end'] for record in records), name + ': drain ends before request')
    equal(trial['successes_per_second_including_drain'], len(records) / elapsed, name + ': throughput')
    in_window = sum(record['end'] <= trial['configured_seconds'] for record in records)
    equal(trial['completed_by_deadline'], in_window, name + ': deadline completions')
    equal(trial['successful_by_deadline'], in_window, name + ': deadline successes')
    for key, field in (('request_latency', 'service_seconds'), ('offered_latency', 'offered_seconds')):
        times = [record[field] * 1000 for record in records]
        for percentile in (50, 95, 99):
            equal(trial[key][f'p{percentile}_ms'], nearest(times, percentile), name + f': {key} p{percentile}')
        equal(trial[key]['max_ms'], max(times), name + ': ' + key + ' max')
    for percentile in (50, 95, 99):
        equal(trial['scheduler_delay_ms'][f'p{percentile}'],
              nearest([record['scheduler_delay'] * 1000 for record in records], percentile),
              name + f': scheduler p{percentile}')
    verify_http_resources(trial)
    return trial, metadata


def verify_http(base, repository):
    directory = base / 'http-comparison'
    campaign = read(directory / 'comparison-summary.json')
    require(campaign['complete'] is True, 'HTTP comparison incomplete')
    equal(campaign['runner_sha256'], digest(repository / 'tools/runtime_comparison.py'), 'HTTP runner hash')
    roots = {variant: base / (variant + '-source') for variant in ('baseline', 'candidate')}
    for variant, root in roots.items():
        verify_hashes(root, campaign['source_sha256'][variant])
    for path, expected in campaign['source_sha256']['baseline'].items():
        if path.startswith(('examples/', 'tools/')):
            equal(campaign['source_sha256']['candidate'].get(path), expected, 'unchanged HTTP app/driver: ' + path)
    config = campaign['configuration']
    equal(len(campaign['trials']), config['repeats'] * len(config['clients']) * 2, 'HTTP campaign trial count')
    counts = Counter((row['variant'], row['concurrency']) for row in campaign['trials'])
    for variant in roots:
        for clients in config['clients']:
            equal(counts[variant, clients], config['repeats'], f'{variant}/c{clients} repeat count')
    for index in range(0, len(campaign['trials']), 2):
        first, second = campaign['trials'][index:index + 2]
        repeat = int(first['name'].split('-')[0][1:])
        equal(second['name'].split('-')[0], f'r{repeat}', 'HTTP pair repetition')
        equal(first['concurrency'], second['concurrency'], 'HTTP pair concurrency')
        equal([first['variant'], second['variant']],
              ['baseline', 'candidate'] if repeat % 2 else ['candidate', 'baseline'], 'HTTP alternating pair order')
    artifacts, sources, total = set(), [], 0
    rows = campaign['trials'] + ([campaign['soak']] if 'soak' in campaign else [])
    for row in rows:
        trial, metadata = verify_http_trial(directory, row)
        equal(metadata['source_sha256'], campaign['source_sha256'][row['variant']], trial['name'] + ': runtime identity')
        artifacts.add(trial['artifact_sha256'])
        sources.append(trial['source_sha256'])
        total += trial['successful']
    equal(len(artifacts), 1, 'HTTP app artifact equality across all runtimes/trials')
    require(all(source == sources[0] for source in sources), 'HTTP staged app sources differ')
    groups = {}
    for key, recorded in campaign['summary'].items():
        selected = [row for row in campaign['trials'] if f"{row['variant']}-c{row['concurrency']}" == key]
        values = [row['successes_per_second_including_drain'] for row in selected]
        equal(recorded['throughput_each'], values, key + ': constituent throughputs')
        equal(recorded['mean'], statistics.mean(values), key + ': mean')
        equal(recorded['sample_stdev'], statistics.stdev(values) if len(values) > 1 else None, key + ': sample SD')
        groups[key] = {'mean_correct_requests_per_second': statistics.mean(values),
                       'sample_stdev': statistics.stdev(values) if len(values) > 1 else None,
                       'p99_ms_range': [min(row['request_latency']['p99_ms'] for row in selected),
                                        max(row['request_latency']['p99_ms'] for row in selected)]}
    equal(set(campaign['summary']), {f'{variant}-c{count}' for variant, count in counts}, 'HTTP summary groups')
    return {'trials': len(campaign['trials']), 'has_soak': 'soak' in campaign,
            'recorded_correct_requests_including_soak': total, 'artifact_sha256': next(iter(artifacts)),
            'groups': groups}


def operation_summary(rows):
    require(bool(rows), 'empty storage timing series')
    for row in rows:
        require(row['correct'] is True, 'storage operation recorded incorrect')
        require(all(type(row[key]) is int and row[key] >= 0 for key in ('wall_ns', 'cpu_ns')),
                'storage timing must be nonnegative integer nanoseconds')
    walls = [row['wall_ns'] / 1e6 for row in rows]
    return {'operations': len(rows), 'median_ms': statistics.median(walls),
            'min_ms': min(walls), 'max_ms': max(walls),
            'total_wall_seconds': sum(row['wall_ns'] for row in rows) / 1e9,
            'total_process_cpu_seconds': sum(row['cpu_ns'] for row in rows) / 1e9}


def verify_storage(base, variant, cache):
    directory = base / (('storage-cache-' if cache else 'storage-') + variant)
    campaign = read(directory / 'summary.json')
    require(campaign['measurement_complete'] is True and campaign['artifacts_unchanged'] is True,
            directory.name + ': campaign incomplete/changed')
    root = base / (variant + '-source')
    if cache:
        before = campaign['identity_before']
        equal(campaign['identity_after'], before, directory.name + ': final identity')
        verify_hashes(root, before['runtime_sha256'])
        equal(before['harness_sha256'], digest(Path(__file__).parent / 'storage_cache_probe.py'), 'cache harness hash')
    else:
        verify_hashes(root, campaign['frozen_sha256'])
    config = campaign['configuration']
    equal(len(campaign['trials']), config['repeats'] * len(config['sizes_mib']), directory.name + ': trial count')
    scenarios = Counter((trial['repetition'], trial['size_mib']) for trial in campaign['trials'])
    equal(scenarios, Counter((repeat + 1, size) for repeat in range(config['repeats'])
                            for size in config['sizes_mib']), directory.name + ': scenario coverage')
    total = 0
    for execution, trial in enumerate(campaign['trials']):
        equal(trial, read(directory / f'trial-{execution:02d}.json'), directory.name + ': raw trial equality')
        equal(trial['execution_index'], execution, directory.name + ': execution sequence')
        if cache:
            require(trial['measurement_complete'] is True and trial['final_integrity_correct'] is True,
                    directory.name + ': trial correctness')
            equal(trial['returncode'], 0, directory.name + ': worker return code')
            equal(trial['identity_before'], before, directory.name + ': worker initial identity')
            equal(trial['identity_after'], before, directory.name + ': worker final identity')
            all_rows = trial['operations']
            equal([row['execution_index'] for row in all_rows], list(range(config['reads'] * 2)), 'cache operation sequence')
            for pair in range(config['reads']):
                rows = all_rows[pair * 2:pair * 2 + 2]
                equal([row['pair_index'] for row in rows], [pair, pair], 'cache pair indices')
                wanted = ['hot_repeated_key', 'cold_fresh_store']
                if (pair % 2 == 0) != trial['first_pair_hot_first']:
                    wanted.reverse()
                equal([row['condition'] for row in rows], wanted, 'cache paired condition order')
            operations = {condition: [row for row in all_rows if row['condition'] == condition]
                          for condition in ('hot_repeated_key', 'cold_fresh_store')}
            for row in all_rows:
                require(type(row['result']) is str and row['result'] == '0', 'cache timed read result differs')
            equal(len(trial['external_updates']), config['updates'], 'cache external update count')
            for index, update in enumerate(trial['external_updates']):
                equal(update['index'], index, 'cache update index')
                equal(update['returncode'], 0, 'cache writer return code')
                require(update['correct'] is True and type(update['actual']) is str
                        and update['actual'] == update['expected'] == str(index + 1), 'cache stale external update')
        else:
            require(trial['correct'] is True, directory.name + ': trial correctness')
            operations = trial['operations']
            equal(set(operations), {'read', 'compare_exchange'}, 'storage operation names')
            for operation, rows in operations.items():
                count = config['reads'] if operation == 'read' else config['writes']
                equal([row['index'] for row in rows], list(range(count)), 'storage operation indices')
        for operation, rows in operations.items():
            computed = operation_summary(rows)
            for key, value in computed.items():
                equal(trial['summary'][operation][key], value, f'{directory.name}/{operation}/{key}')
            if cache:
                equal(trial['summary'][operation]['correct_operations'], len(rows), 'cache correct count')
            total += len(rows)
    grouping = 'condition' if cache else 'operation'
    for group in campaign['summary']:
        medians = [trial['summary'][group[grouping]]['median_ms'] for trial in campaign['trials']
                   if trial['size_mib'] == group['size_mib']]
        equal(group['trial_medians_ms'], medians, directory.name + ': group samples')
        equal(group['median_of_trial_medians_ms'], statistics.median(medians), directory.name + ': group median')
        equal(group['min_trial_median_ms'], min(medians), directory.name + ': group min')
        equal(group['max_trial_median_ms'], max(medians), directory.name + ': group max')
    expected_groups = {(size, operation) for size in set(config['sizes_mib']) for operation in operations}
    equal({(group['size_mib'], group[grouping]) for group in campaign['summary']}, expected_groups,
          directory.name + ': summary group coverage')
    return {'trials': len(campaign['trials']), 'timed_operations': total, 'summary': campaign['summary']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    base = args.evidence.resolve()
    repository = Path(__file__).resolve().parents[1]
    require(not args.output.exists(), 'verification output already exists; preserve prior evidence')
    report = {'checker_sha256': digest(Path(__file__)), 'passed': False, 'checks': {},
              'limits': ['Separate arithmetic/code review within the same shared workspace; not external attestation.',
                         'Successful HTTP bodies were not retained: verifies recorded status/correctness flags, not reconstructed bodies.',
                         'Original storage-size sweeps retain correctness flags rather than read values; cache probe retains actual read values.',
                         'Source and artifact hashes establish consistency of retained records, not independent authenticity.',
                         'No speed acceptance threshold, production-capacity conclusion, or memory-leak absence claim.']}
    try:
        report['checks']['http'] = verify_http(base, repository)
        for variant in ('baseline', 'candidate'):
            for cache in (False, True):
                name = ('storage-cache-' if cache else 'storage-') + variant
                report['checks'][name] = verify_storage(base, variant, cache)
        report['passed'] = True
    except BaseException as error:
        report['error'] = repr(error)
        raise
    finally:
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
