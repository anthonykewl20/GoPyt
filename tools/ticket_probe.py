#!/usr/bin/env python3
"""External, deterministic acceptance tests for the GoPyT ticket HTTP app."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {'outcome', 'id', 'title', 'status', 'version'}


def fingerprint(directory):
    """Hash relevant compiler, app, and harness bytes; omit generated state."""
    paths = sorted(p for p in directory.rglob('*') if p.is_file()
                   and p.suffix in {'.py', '.gopyt', '.toml', '.lock', '.md'}
                   and p.name != '.gopyt-transaction.lock'
                   and not any(x in p.parts for x in ('__pycache__', 'build', '.gopyt', '.gopyt-state')))
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def environment():
    result = {'python': sys.version, 'executable': sys.executable, 'platform': platform.platform(),
              'cpu_count': os.cpu_count(), 'clock': vars(time.get_clock_info('perf_counter')),
              'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'compiler_sha256': fingerprint(ROOT / 'gopyt'),
              'harness_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in (ROOT / 'tools').glob('ticket_*.py')}}
    if hasattr(os, 'sched_getaffinity'):
        result['cpu_affinity'] = sorted(os.sched_getaffinity(0))
    for name, path in [('cpuinfo', '/proc/cpuinfo'), ('meminfo', '/proc/meminfo'),
                       ('cgroup_cpu_max', '/sys/fs/cgroup/cpu.max')]:
        try:
            result[name] = Path(path).read_text()
        except OSError:
            pass
    if hasattr(os, 'getloadavg'):
        result['load_average'] = os.getloadavg()
    return result


class Client:
    def __init__(self, port, timeout=10):
        self.conn = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)

    def request(self, method, path, body=None, *, raw=None, content_type='application/json', headers=None):
        payload = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        self.conn.request(method, path, body=payload, headers={'Content-Type': content_type, **(headers or {})})
        response = self.conn.getresponse()
        data = response.read()
        try:
            value = json.loads(data) if data else None
        except (ValueError, UnicodeError):
            value = {'_invalid_json': data.decode(errors='replace')}
        return response.status, value

    def close(self):
        self.conn.close()


class Server:
    """Real CLI subprocess in a fresh copied project, retaining state on restart."""
    def __init__(self, source=ROOT / 'examples/tickets', shared_root=None):
        self.temp = tempfile.TemporaryDirectory(prefix='gopyt-ticket-')
        self.root = Path(self.temp.name) / 'tickets'
        if shared_root is None:
            shutil.copytree(source, self.root, ignore=shutil.ignore_patterns('build', '.gopyt-state', '.gopyt', '.gopyt-transaction.lock'))
        else:
            self.root = shared_root
        self.source_hashes = fingerprint(self.root)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.env = dict(os.environ, PYTHONPATH=str(ROOT), GOPYT_HTTP_ADDR=f'127.0.0.1:{self.port}',
                        PYTHONHASHSEED='0')
        self.proc = None
        self.starts = []
        self.log = tempfile.TemporaryFile(mode='w+b')

    def command(self, command):
        started = time.perf_counter()
        result = subprocess.run([sys.executable, '-m', 'gopyt', command], cwd=self.root,
                                env=self.env, capture_output=True, timeout=60)
        elapsed = time.perf_counter() - started
        if result.returncode:
            raise AssertionError(f'{command} failed: {result.stdout.decode()} {result.stderr.decode()}')
        return elapsed

    def start(self):
        started = time.perf_counter()
        self.proc = subprocess.Popen([sys.executable, '-m', 'gopyt', 'run', 'tickets.serve'],
                                     cwd=self.root, env=self.env, stdout=self.log, stderr=self.log)
        while time.perf_counter() - started < 30:
            if self.proc.poll() is not None:
                self.log.seek(0)
                raise AssertionError(f'server exited {self.proc.returncode}: {self.log.read().decode()}')
            client = Client(self.port, timeout=.25)
            try:
                status, body = client.request('GET', '/tickets/readiness-missing')
                if status == 200 and isinstance(body, dict) and body.get('outcome') == 'not_found':
                    self.starts.append(time.perf_counter() - started)
                    return self.starts[-1]
            except (OSError, http.client.HTTPException):
                pass
            finally:
                client.close()
            time.sleep(.01)
        raise AssertionError('server failed readiness within 30s')

    def stop(self, abrupt=False):
        if self.proc is not None and self.proc.poll() is None:
            if abrupt:
                self.proc.kill()
            else:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
                raise AssertionError('graceful server shutdown exceeded 10s; forced termination required')

    def close(self):
        try:
            self.stop()
        finally:
            self.log.close()
            self.temp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def envelope(outcome, ident, title='', status='', version=0):
    return dict(outcome=outcome, id=ident, title=title, status=status, version=version)


def acceptance(output, source=ROOT / 'examples/tickets'):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    report = {'environment': environment(), 'cases': [], 'passed': False}
    server = Server(source)
    client = None
    try:
        report['source_sha256'] = server.source_hashes
        report['check_seconds'] = server.command('check')
        report['language_test_seconds'] = server.command('test')
        server.start()
        client = Client(server.port)

        def check(name, method, path, expected, body=None, status=200, **kwargs):
            actual_status, actual = client.request(method, path, body, **kwargs)
            success = actual_status == status and actual == expected
            if status == 200:
                success = success and isinstance(actual, dict) and set(actual) == FIELDS
                success = success and type(actual['version']) is int
            report['cases'].append({'name': name, 'method': method, 'path': path, 'expected_status': status,
                                    'actual_status': actual_status, 'expected': expected, 'actual': actual,
                                    'passed': success})
            if not success:
                raise AssertionError(f'{name}: expected {status} {expected!r}, got {actual_status} {actual!r}')
            return actual

        check('missing', 'GET', '/tickets/absent', envelope('not_found', 'absent'))
        check('create', 'POST', '/tickets', envelope('created', 't1', 'First', 'open', 1), {'id': 't1', 'title': 'First'})
        check('get', 'GET', '/tickets/t1', envelope('ok', 't1', 'First', 'open', 1))
        check('duplicate', 'POST', '/tickets', envelope('conflict', 't1', 'First', 'open', 1), {'id': 't1', 'title': 'Other'})
        for ident in ['', 'a'*65, 'a.b', 'a/b', 'a b', 'é', '../x', 'a\n', 'a\x00', '🙂']:
            check('invalid-id-' + repr(ident), 'POST', '/tickets', envelope('invalid', ident), {'id': ident, 'title': 'Title'})
        for title in ['', 'a'*201, '🙂'*201]:
            check('invalid-title-' + str(len(title)), 'POST', '/tickets', envelope('invalid', 'invalid-title'),
                  {'id': 'invalid-title', 'title': title})
        for ident, title in [('A_9-'+'x'*60, '🙂'*200), ('unicode', 'Café 東京'), ('punct', 'line\nquote"\\')]:
            check('valid-boundary-create-' + ident, 'POST', '/tickets', envelope('created', ident, title, 'open', 1),
                  {'id': ident, 'title': title})
            check('valid-boundary-read-' + ident, 'GET', '/tickets/' + ident, envelope('ok', ident, title, 'open', 1))
        for index, raw in enumerate([b'{', b'null', b'[]', b'{}', b'{"id":"x"}', b'{"id":1,"title":"x"}',
                                     b'{"id":"x","title":false}', b'{"id":"x","title":"\xff"}']):
            check(f'malformed-create-{index}', 'POST', '/tickets', None, status=400, raw=raw)
        check('unknown-route', 'GET', '/unrecognized', None, status=404)
        check('wrong-method', 'GET', '/tickets', None, status=404)
        check('wrong-content-type', 'POST', '/tickets', None, {'id':'wrongtype','title':'x'}, status=400, content_type='text/plain')
        for status in ['closed', 'open', 'IN_PROGRESS', '', 'in_progress ']:
            check('illegal-transition-' + status, 'POST', '/tickets/t1/transition', envelope('invalid', 't1'),
                  {'expected_version': 1, 'status': status})
        for version in [-1, 0]:
            check('invalid-version-' + str(version), 'POST', '/tickets/t1/transition', envelope('invalid', 't1'),
                  {'expected_version': version, 'status': 'in_progress'})
        for version in [2, 9223372036854775807]:
            check('stale-version-' + str(version), 'POST', '/tickets/t1/transition', envelope('conflict', 't1', 'First', 'open', 1),
                  {'expected_version': version, 'status': 'in_progress'})
        for index, version in enumerate([True, '1', 1.5, 9223372036854775808]):
            check('malformed-version-' + str(index), 'POST', '/tickets/t1/transition', None,
                  {'expected_version': version, 'status': 'in_progress'}, status=400)
        check('transition-missing', 'POST', '/tickets/absent/transition', envelope('not_found', 'absent'),
              {'expected_version': 1, 'status': 'in_progress'})
        check('start', 'POST', '/tickets/t1/transition', envelope('updated', 't1', 'First', 'in_progress', 2),
              {'expected_version': 1, 'status': 'in_progress'})
        check('close', 'POST', '/tickets/t1/transition', envelope('updated', 't1', 'First', 'closed', 3),
              {'expected_version': 2, 'status': 'closed'})
        check('no-reopen', 'POST', '/tickets/t1/transition', envelope('invalid', 't1'), {'expected_version': 3, 'status': 'open'})

        def race(name, method, path, body, expected_winner, ports=None):
            barrier = threading.Barrier(16)
            def request(_):
                conn = Client((ports or [server.port])[_ % len(ports or [server.port])])
                try:
                    barrier.wait(timeout=15)
                    return conn.request(method, path, body)
                finally:
                    conn.close()
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                results = list(pool.map(request, range(16)))
            outcomes = [value.get('outcome') if isinstance(value, dict) else None for _, value in results]
            good = all(code == 200 for code, _ in results) and outcomes.count(expected_winner) == 1 and outcomes.count('conflict') == 15
            report['cases'].append({'name': name, 'results': results, 'passed': good})
            if not good:
                raise AssertionError(f'{name}: {results}')

        race('racing-create', 'POST', '/tickets', {'id': 'race', 'title': 'Race'}, 'created')
        race('racing-transition', 'POST', '/tickets/race/transition', {'expected_version': 1, 'status': 'in_progress'}, 'updated')
        check('race-final-state', 'GET', '/tickets/race', envelope('ok', 'race', 'Race', 'in_progress', 2))
        with Server(source, shared_root=server.root) as peer:
            peer.start()
            race('cross-process-create', 'POST', '/tickets', {'id':'shared', 'title':'Shared'}, 'created', [server.port, peer.port])
            race('cross-process-transition', 'POST', '/tickets/shared/transition', {'expected_version':1, 'status':'in_progress'}, 'updated', [server.port, peer.port])
        check('cross-process-final-state', 'GET', '/tickets/shared', envelope('ok', 'shared', 'Shared', 'in_progress', 2))
        rng = random.Random(731)
        model = {}
        for index in range(100):
            ident = 'model-' + str(rng.randrange(10))
            if ident not in model:
                title = 'Title ' + ident
                check(f'model-create-{index}', 'POST', '/tickets', envelope('created', ident, title, 'open', 1), {'id':ident,'title':title})
                model[ident] = [title, 'open', 1]
            else:
                title, state, version = model[ident]
                if state != 'closed' and rng.random() < .6:
                    state = 'in_progress' if state == 'open' else 'closed'
                    check(f'model-transition-{index}', 'POST', f'/tickets/{ident}/transition', envelope('updated', ident, title, state, version+1),
                          {'expected_version':version, 'status':state})
                    model[ident] = [title, state, version+1]
                else:
                    check(f'model-read-{index}', 'GET', '/tickets/' + ident, envelope('ok', ident, title, state, version))
        client.close()
        server.stop(abrupt=True)
        server.start()
        client = Client(server.port)
        check('restart-closed', 'GET', '/tickets/t1', envelope('ok', 't1', 'First', 'closed', 3))
        check('restart-raced', 'GET', '/tickets/race', envelope('ok', 'race', 'Race', 'in_progress', 2))
        for ident, (title, state, version) in model.items():
            check('restart-' + ident, 'GET', '/tickets/' + ident, envelope('ok', ident, title, state, version))
        check('restart-cross-process', 'GET', '/tickets/shared', envelope('ok', 'shared', 'Shared', 'in_progress', 2))
        assert fingerprint(server.root) == report['source_sha256'], 'app source changed during acceptance'
        report['startup_seconds'] = server.starts
        report['artifact_sha256'] = hashlib.sha256((server.root / 'build/out.gobyte').read_bytes()).hexdigest()
        client.close()
        server.stop()
        report['graceful_shutdown_passed'] = True
        report['passed'] = True
    except BaseException as error:
        report['error'] = repr(error)
        raise
    finally:
        if client:
            client.close()
        try:
            server.close()
        finally:
            (output / 'acceptance.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source', type=Path, default=ROOT / 'examples/tickets')
    args = parser.parse_args()
    report = acceptance(args.output, args.source)
    print(f"Passed {len(report['cases'])} external acceptance cases")


if __name__ == '__main__':
    main()
