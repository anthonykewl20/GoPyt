#!/usr/bin/env python3
"""Paired TCP_NODELAY control for small persistent GoPyT HTTP responses.

This isolates transport behavior with an existing conformance handler. It is not
an application-throughput comparison. Each condition starts a fresh server,
opens one persistent connection, excludes one explicitly recorded warmup
request, then records every measured request. Alternate condition order to
reduce monotonic host drift; no noisy timing threshold determines correctness.
"""
import argparse
import contextlib
import hashlib
import http.client
import io
import json
from pathlib import Path
import statistics
import sys
import time

from ticket_probe import ROOT, environment
sys.path.insert(0, str(ROOT))
from gopyt.test_app_runtime import running_server


def run(output, repeats=3, requests=15):
    report = {'environment': environment(), 'repeats': repeats,
              'probe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'measured_requests_per_condition': requests, 'trials': [],
              'passed': False,
              'method': 'Alternating TCP_NODELAY off/on order, fresh process-local VM/server per condition; one excluded first request then one persistent sequential client; elapsed is perf_counter_ns; no performance threshold.'}
    output.mkdir(parents=True, exist_ok=True)
    try:
        for repeat in range(repeats):
            order = (False, True) if repeat % 2 == 0 else (True, False)
            for enabled in order:
                trial = {'repeat': repeat + 1, 'tcp_nodelay': enabled, 'raw_elapsed_ns': []}
                report['trials'].append(trial)
                with contextlib.redirect_stdout(io.StringIO()), running_server(TCP_NODELAY=enabled) as (vm, port):
                    trial['artifact_sha256'] = hashlib.sha256(Path(vm.root, 'build/out.gobyte').read_bytes()).hexdigest()
                    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                    try:
                        for index in range(requests + 1):
                            start = time.perf_counter_ns()
                            connection.request('GET', '/echo/abc')
                            response = connection.getresponse()
                            body = response.read()
                            elapsed = time.perf_counter_ns() - start
                            if (response.status, body) != (200, b'{"amount":3}'):
                                raise AssertionError((response.status, body))
                            if index == 0:
                                trial['excluded_connection_warmup_ns'] = elapsed
                            else:
                                trial['raw_elapsed_ns'].append(elapsed)
                    finally:
                        connection.close()
                trial['median_ms'] = statistics.median(trial['raw_elapsed_ns']) / 1_000_000
        report['passed'] = True
    except BaseException as error:
        report['error'] = repr(error)
        raise
    finally:
        (output / 'transport.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--requests', type=int, default=15)
    args = parser.parse_args()
    if args.repeats < 1 or args.requests < 1:
        parser.error('repeats and requests must be positive')
    result = run(args.output, args.repeats, args.requests)
    for condition in (False, True):
        medians = [round(t['median_ms'], 3) for t in result['trials'] if t['tcp_nodelay'] == condition]
        print(f'TCP_NODELAY={condition}: trial median milliseconds {medians}')
