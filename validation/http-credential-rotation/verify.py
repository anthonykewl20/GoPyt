"""Offline literal authorization and round-budget oracle; no runtime imports."""
import json
from pathlib import Path
import sys


def verify(directory):
    directory = Path(directory)
    inputs = json.loads((directory/'inputs.json').read_text())
    report = json.loads((directory/'report.json').read_text())
    rows = [json.loads(line) for line in (directory/'rounds.jsonl').read_text().splitlines()]
    assert report['status'] == 'passed'
    assert [row['round'] for row in rows] == list(range(len(rows)))
    assert report['rounds'] == len(rows) and report['requests'] == len(rows)*8
    for row in rows:
        requests = row['requests']
        assert len(requests) == 8
        assert sorted(request['role'] for request in requests) == sorted(['admitted_old'] + ['new']*3 + ['revoked']*4)
        for request in requests:
            assert (request['status'], request['body']) == ((401, '') if request['role'] == 'revoked' else (200, '{"amount":3}'))
        assert 0 < row['elapsed_ns'] <= inputs['protocol']['maximum_round_seconds']*1e9
    assert report['negative_control_rejected']
    if inputs['mode'] == 'measured':
        assert len(rows) >= inputs['protocol']['minimum_rounds']
        assert report['seconds'] >= inputs['protocol']['minimum_seconds']
    print(f'Verified {len(rows)} rounds and {len(rows)*8} requests ({inputs["mode"]})')


if __name__ == '__main__':
    verify(sys.argv[1])
