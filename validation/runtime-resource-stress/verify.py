"""Recheck retained episode facts without loading or executing GoPyt."""
import argparse
import json
from pathlib import Path
import oracle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    inputs = json.loads((args.directory / 'inputs.json').read_text())
    report = json.loads((args.directory / 'report.json').read_text())
    config = inputs['config']
    oracle.require(report['status'] == 'passed', 'campaign failed')
    oracle.require(report['mode'] == inputs['mode'], 'mode mismatch')
    oracle.require([p['phase'] for p in report['phases']] == config['phases'], 'missing or reordered phase')
    rows = [json.loads(line) for line in (args.directory / 'episodes.jsonl').read_text().splitlines()]
    oracle.require(set(r['phase'] for r in rows) == set(config['phases']), 'raw phase coverage')
    for phase in report['phases']:
        records = [r for r in rows if r['phase'] == phase['phase']]
        oracle.require(len(records) == phase['episodes'], 'episode count mismatch')
        oracle.require([r['iteration'] for r in records] == list(range(len(records))), 'episode sequence')
        if inputs['mode'] == 'measured':
            oracle.require(not inputs['dirty_source'], 'dirty measured inputs')
            oracle.require(len(records) >= config['minimum_measured_episodes_per_phase'], 'insufficient episodes')
            oracle.require(phase['seconds'] >= config['minimum_measured_seconds_per_phase'], 'insufficient duration')
        for row in records:
            oracle.validate(phase['phase'], row['facts'], config)
            oracle.validate_resources(row['after_gc'], phase['baseline'], config)
            oracle.require(0 < row['elapsed_ns'] <= config['maximum_episode_seconds'] * 1e9, 'latency budget')
        oracle.require(phase['max_ns'] == max(r['elapsed_ns'] for r in records), 'maximum mismatch')
        oracle.require(phase['final'] == records[-1]['after_gc'], 'final resource mismatch')
    oracle.require(len(report['negative_controls']) == 6 and all(c['rejected'] for c in report['negative_controls']), 'negative controls')
    oracle.require(not report['sampler_errors'], 'sampler errors')
    print(f"Verified {len(rows)} {inputs['mode']} episodes across {len(report['phases'])} phases")


if __name__ == '__main__':
    main()
