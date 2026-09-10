"""Frozen concurrent credential replacement with literal HTTP outcome checks."""
import argparse
import concurrent.futures
import contextlib
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import statistics
import sys
import tempfile
import threading
import time
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from gopyt.test_app_runtime import running_server
from gopyt.toolchain import FINGERPRINT
from gopyt.values import UNIT

HERE = Path(__file__).resolve().parent


def validate(rows, config):
    expected_counts = config['requests_per_round']
    assert {role:sum(row['role'] == role for row in rows) for role in expected_counts} == expected_counts
    for row in rows:
        expected = config['expected_statuses'][row['role']]
        assert row['status'] == expected, (row['role'], row['status'], expected)
        assert row['body'] == ('{"amount":3}' if expected == 200 else '')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    config = json.loads((HERE/'protocol.json').read_text())
    inputs = list((ROOT/'gopyt').glob('*.py')) + list(HERE.glob('*.py')) + [HERE/'protocol.json']
    hashes = {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    args.output.mkdir(exist_ok=False, parents=True)
    identity = dict(python=sys.version, runtime_sha256=FINGERPRINT.hex(), source_sha256=hashes,
                    protocol=config, mode='smoke' if args.smoke else 'measured')
    (args.output/'inputs.json').write_text(json.dumps(identity, indent=2)+'\n')
    latencies, tokens, failure, rejected_control = [], [], None, False
    started = None
    try:
        with tempfile.TemporaryDirectory() as temporary, (args.output/'execution.log').open('w') as log:
            base = Path(temporary).resolve(); path = base/'token'
            def replace(value):
                next_path = base/'next'
                fd = os.open(next_path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as stream:
                    stream.write(value); stream.flush(); os.fsync(stream.fileno())
                next_path.replace(path)
                directory = os.open(base, os.O_RDONLY|os.O_DIRECTORY)
                try: os.fsync(directory)
                finally: os.close(directory)
            current = secrets.token_hex(24); tokens.append(current); replace(current)
            env = {'GOPYT_SECURITY_PROFILE':'development', 'GOPYT_HTTP_TOKEN_FILE':str(path),
                   'GOPYT_STORE_ANCHOR_DIR':''}
            with patch.dict(os.environ, env), contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                with running_server(MAX_HANDLERS=config['workers'], QUEUE=config['queue']) as (vm, port):
                    vm.natives = dict(vm.natives)
                    gate = {}
                    def handler(*_):
                        with gate['lock']:
                            first = gate['calls'] == 0
                            gate['calls'] += 1
                        if first:
                            gate['entered'].set()
                            if not gate['release'].wait(config['maximum_round_seconds']):
                                raise AssertionError('admitted request release watchdog')
                        return UNIT
                    vm.natives['core.log.write'] = handler
                    def request(role, token):
                        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=config['request_timeout_seconds'])
                        try:
                            connection.request('GET', '/echo/abc', headers={'Authorization':'Bearer '+token})
                            response = connection.getresponse()
                            return dict(role=role, status=response.status, body=response.read().decode())
                        finally: connection.close()
                    def round_(pool):
                        nonlocal current
                        gate.clear(); gate.update(lock=threading.Lock(), calls=0, entered=threading.Event(), release=threading.Event())
                        old = current
                        admitted = pool.submit(request, 'admitted_old', old)
                        try:
                            assert gate['entered'].wait(config['request_timeout_seconds']), 'handler admission watchdog'
                            current = secrets.token_hex(24); tokens.append(current); replace(current)
                            futures = [pool.submit(request, role, current if role == 'new' else old)
                                       for role in ['new']*3 + ['revoked']*4]
                            rows = [future.result(config['maximum_round_seconds']) for future in futures]
                        finally: gate['release'].set()
                        rows.append(admitted.result(config['maximum_round_seconds']))
                        assert gate['calls'] == 4, 'unexpected handler admission count'
                        return rows
                    with concurrent.futures.ThreadPoolExecutor(max_workers=config['clients']) as pool:
                        for _ in range(0 if args.smoke else config['warmup_rounds']):
                            validate(round_(pool), config)
                        started = time.monotonic()
                        with (args.output/'rounds.jsonl').open('w') as raw:
                            minimum = 2 if args.smoke else config['minimum_rounds']
                            seconds = 0 if args.smoke else config['minimum_seconds']
                            while len(latencies) < minimum or time.monotonic()-started < seconds:
                                begin = time.perf_counter_ns(); rows = round_(pool); elapsed = time.perf_counter_ns()-begin
                                raw.write(json.dumps(dict(round=len(latencies), elapsed_ns=elapsed, requests=rows))+'\n'); raw.flush()
                                validate(rows, config)
                                assert elapsed <= config['maximum_round_seconds']*1e9, 'round latency budget'
                                latencies.append(elapsed)
                                if not rejected_control:
                                    wrong = [dict(row) for row in rows]; wrong[0]['status'] = 401
                                    try: validate(wrong, config)
                                    except AssertionError: rejected_control = True
                                    assert rejected_control, 'bad authorization outcome accepted'
            log.flush()
        leaked = (args.output/'execution.log').read_text()
        assert all(token not in leaked for token in tokens), 'credential exposed in execution log'
        assert all(hashlib.sha256(p.read_bytes()).hexdigest() == hashes[str(p.relative_to(ROOT))] for p in inputs), 'source changed'
    except BaseException:
        failure = traceback.format_exc(); (args.output/'failure.txt').write_text(failure)
    ordered = sorted(latencies)
    report = dict(status='failed' if failure else 'passed', rounds=len(latencies), requests=len(latencies)*8,
                  seconds=None if started is None else time.monotonic()-started,
                  max_ns=max(latencies, default=0), mean_ns=statistics.mean(latencies) if latencies else 0,
                  p99_ns=ordered[int((len(ordered)-1)*.99)] if ordered else 0,
                  negative_control_rejected=rejected_control, scope=config['scope'])
    (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(failure or json.dumps(report))
    return 1 if failure else 0


if __name__ == '__main__':
    raise SystemExit(main())
