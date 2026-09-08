#!/usr/bin/env python3
"""Independent ticket fault-injection and two-process HTTP race probes.

The HTTP oracle comes from examples/tickets/README.md. Storage injection is
explicitly white-box setup; all resulting behavior is checked through HTTP.
"""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

from ticket_probe import ROOT, Client, Server, environment
sys.path.insert(0, str(ROOT))
from gopyt.storage import Store, DIRECTORY, DATABASE


def run(output):
    report = {'environment': environment(), 'passed': False, 'checks': []}
    output.mkdir(parents=True, exist_ok=True)
    def record(name, condition, actual):
        report['checks'].append({'name': name, 'passed': bool(condition), 'actual': actual})
        if not condition:
            raise AssertionError(f'{name}: {actual!r}')
    with Server() as server:
        peer = None
        client = None
        try:
            server.start()
            client = Client(server.port)
            report['source_sha256'] = server.source_hashes
            report['artifact_sha256'] = hashlib.sha256((server.root / 'build/out.gobyte').read_bytes()).hexdigest()
            # Missing-record precedence: field validation wins before lookup.
            for value in (-1, 0):
                actual = client.request('POST', '/tickets/absent/transition', {'expected_version': value, 'status': 'in_progress'})
                record('invalid-version-before-missing-' + str(value), actual == (200, dict(outcome='invalid', id='absent', title='', status='', version=0)), actual)
            for status in ('unknown', ''):
                actual = client.request('POST', '/tickets/absent/transition', {'expected_version': 1, 'status': status})
                record('invalid-status-before-missing-' + status, actual == (200, dict(outcome='invalid', id='absent', title='', status='', version=0)), actual)
            # Durable bytes may be valid JSON but violate business invariants.
            good = dict(id='corrupt', title='Title', status='open', version=1)
            bad_records = [dict(good, id='different'), dict(good, title=''), dict(good, title='x'*201),
                           dict(good, status='unknown'), dict(good, status='closed'), dict(good, version=2),
                           dict(good, version=0), dict(good, version=True), dict(good, extra=1), {}, None]
            for index, bad in enumerate(bad_records):
                Store(server.root).put('tickets:corrupt', json.dumps(bad))
                for method, path, body in [('GET', '/tickets/corrupt', None),
                                           ('POST', '/tickets', {'id': 'corrupt', 'title': 'New'}),
                                           ('POST', '/tickets/corrupt/transition', {'expected_version':1, 'status':'in_progress'})]:
                    actual = client.request(method, path, body)
                    record(f'malformed-stored-{index}-{method}-{path}', actual == (200, dict(outcome='storage_error', id='corrupt', title='', status='', version=0)), actual)
            # Run a second real CLI process against exactly the same package.
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                peer_port = reservation.getsockname()[1]
            peer_env = dict(server.env, GOPYT_HTTP_ADDR=f'127.0.0.1:{peer_port}')
            peer = subprocess.Popen([sys.executable, '-m', 'gopyt', 'run', 'tickets.serve'],
                                    cwd=server.root, env=peer_env, stdout=server.log, stderr=server.log)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                probe = Client(peer_port, timeout=.2)
                try:
                    code, value = probe.request('GET', '/tickets/peer-ready')
                    if code == 200 and value['outcome'] == 'not_found':
                        break
                except (OSError, ValueError):
                    time.sleep(.01)
                finally:
                    probe.close()
            else:
                raise AssertionError('peer server failed readiness')
            for operation, path, body, winner, expected_state, version in [
                    ('create', '/tickets', {'id':'multiprocess','title':'Shared'}, 'created', 'open', 1),
                    ('start', '/tickets/multiprocess/transition', {'expected_version':1,'status':'in_progress'}, 'updated', 'in_progress', 2),
                    ('close', '/tickets/multiprocess/transition', {'expected_version':2,'status':'closed'}, 'updated', 'closed', 3)]:
                barrier = threading.Barrier(16)
                def race(index):
                    racer = Client(server.port if index % 2 else peer_port)
                    try:
                        barrier.wait(5)
                        return racer.request('POST', path, body)
                    finally:
                        racer.close()
                with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                    results = list(pool.map(race, range(16)))
                expected_winner = dict(outcome=winner, id='multiprocess', title='Shared', status=expected_state, version=version)
                expected_loser = dict(expected_winner, outcome='conflict')
                record('two-process-' + operation, results.count((200, expected_winner)) == 1 and results.count((200, expected_loser)) == 15, results)
            # Schema corruption must fail closed; invalid request must still be invalid.
            client.close()
            peer.terminate()
            peer.wait(10)
            if peer.returncode != 0:
                raise AssertionError(f'peer failed graceful shutdown: {peer.returncode}')
            peer = None
            server.stop()
            database = server.root / DIRECTORY / DATABASE
            database.write_bytes(b'deliberately corrupt sqlite')
            # Readiness normally reads storage; start directly for this failure probe.
            server.proc = subprocess.Popen([sys.executable, '-m', 'gopyt', 'run', 'tickets.serve'],
                                           cwd=server.root, env=server.env, stdout=server.log, stderr=server.log)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                client = Client(server.port, timeout=.5)
                try:
                    actual = client.request('GET', '/tickets/multiprocess')
                    break
                except OSError:
                    client.close()
                    time.sleep(.02)
            else:
                raise AssertionError('corrupt-state server did not listen')
            record('corrupt-database-fails-closed', actual == (200, dict(outcome='storage_error', id='multiprocess', title='', status='', version=0)), actual)
            actual = client.request('POST', '/tickets', {'id':'bad.id','title':'Title'})
            record('field-validation-before-corrupt-storage', actual == (200, dict(outcome='invalid', id='bad.id', title='', status='', version=0)), actual)
            record('corrupt-database-preserved', database.read_bytes() == b'deliberately corrupt sqlite', database.stat().st_size)
            server.stop()
            report['passed'] = True
        except BaseException as error:
            report['error'] = repr(error)
            raise
        finally:
            if client:
                client.close()
            if peer is not None:
                peer.kill()
                peer.wait(10)
            (output / 'adversarial.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    report = run(args.output)
    print(f"Passed {len(report['checks'])} independent ticket adversarial checks")
