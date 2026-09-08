#!/usr/bin/env python3
"""Summarize sampled RSS in the retained ten-minute HTTP soak.

This reports an observed time series, not a leak test or a memory bound.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    raw = args.trial.read_bytes()
    trial = json.loads(raw)
    if trial['configured_seconds'] != 600 or not trial['integrity_passed']:
        raise ValueError('requires a completed 600-second trial with verified state')

    def window(start, end):
        rows = [row for row in trial['resource_samples']
                if start <= row['elapsed'] < end and row['server']['rss_bytes'] is not None]
        if len(rows) < 2:
            raise ValueError('insufficient Linux RSS samples in declared window')
        times = [row['elapsed'] for row in rows]
        values = [row['server']['rss_bytes'] for row in rows]
        return {'start_seconds': start, 'end_seconds': end, 'sample_count': len(rows),
                'first_rss_bytes': values[0], 'last_rss_bytes': values[-1],
                'min_rss_bytes': min(values), 'max_rss_bytes': max(values),
                'ols_rss_bytes_per_second': statistics.linear_regression(times, values).slope,
                'server_threads': sorted({row['server']['threads'] for row in rows})}

    result = {'source_sha256': hashlib.sha256(raw).hexdigest(),
              'analysis_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'purpose': 'observational RSS time-series summary; not a leak test or proof of memory bounds',
              'successful': trial['successful'], 'errors': trial['errors'],
              'elapsed_seconds': trial['elapsed_including_drain_seconds'],
              'throughput': trial['successes_per_second_including_drain'],
              'p99_ms': trial['request_latency']['p99_ms'],
              'baseline_rss_bytes': trial['baseline']['rss_bytes'],
              'final_rss_bytes': trial['final']['rss_bytes'],
              'peak_sampled_rss_bytes': trial['peak_sampled_rss_bytes'],
              'minutes': [window(i * 60, (i + 1) * 60) for i in range(10)],
              'after_two_minutes': window(120, 600), 'last_five_minutes': window(300, 600),
              'limits': ['RSS sampled at 100 ms can miss peaks; OLS has no confidence interval',
                         'Final sample follows closure of load connections; intermediate samples observe active load',
                         'Single final-runtime run, no equal-duration baseline control; fixed 64-record read workload']}
    args.output.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
