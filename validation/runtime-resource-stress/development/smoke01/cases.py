"""Controlled runtime workloads. Expected outcomes live separately in oracle.py."""
import contextlib
import http.client
import os
import fcntl
from pathlib import Path
import socket
import sqlite3
import threading
import time
from unittest.mock import patch

from gopyt import test_app_runtime as fixture_http
from gopyt.test_parallel_admission import ParallelAdmission
from gopyt.test_transaction_cancellation import TransactionCancellation
from gopyt.storage import DIRECTORY, DATABASE
from gopyt.values import UNIT
from gopyt.vm import Cancelled, Trap


def wait_for(predicate, seconds=10):
    end = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= end:
            raise AssertionError('coordination watchdog expired')
        time.sleep(.002)


def rows(root):
    # Read the actual plaintext SQLite snapshot independently of Store's reader.
    with sqlite3.connect(':memory:') as db:
        db.deserialize(Path(root, DIRECTORY, DATABASE).read_bytes())
        return [list(row) for row in db.execute('SELECT key,value FROM kv ORDER BY key')]


def slow_clients(config, iteration, mark):
    calls = []
    with fixture_http.running_server(MAX_HANDLERS=1, QUEUE=1, REQUEST_TIMEOUT_SECONDS=.2) as (vm, port):
        vm.natives = dict(vm.natives)
        vm.natives['core.log.write'] = lambda *args: calls.append(1) or UNIT
        with socket.create_connection(('127.0.0.1', port), timeout=5) as client:
            client.sendall(b'POST /charge HTTP/1.1\r\nHost: localhost\r\nContent-Length: 1000\r\n\r\n')
            stop = threading.Event()
            def drip():
                while not stop.wait(.025):
                    try:
                        client.sendall(b' ')
                    except OSError:
                        return
            thread = threading.Thread(target=drip)
            thread.start()
            started = time.perf_counter_ns()
            try:
                mark()
                try:
                    received = client.recv(1)
                    closed = received == b''
                except ConnectionResetError:
                    closed = True
                close_ns = time.perf_counter_ns() - started
                calls_before_recovery = len(calls)
            finally:
                stop.set()
                thread.join(5)
                if thread.is_alive():
                    raise AssertionError('dripper survived cleanup')
        with patch('gopyt.server.REQUEST_TIMEOUT_SECONDS', 10.0):
            status, body = fixture_http.request(port)
        return dict(closed=closed, calls_before_recovery=calls_before_recovery,
                    recovery=[status, body.decode()], calls=len(calls), close_ns=close_ns)


def saturation(config, iteration, mark):
    workers, capacity = config['http_workers'], config['http_queue']
    calls = []
    with fixture_http.running_server(MAX_HANDLERS=workers, QUEUE=capacity) as (vm, port):
        vm.natives = dict(vm.natives)
        vm.natives['core.log.write'] = lambda *args: calls.append(1) or UNIT
        with contextlib.ExitStack() as clients:
            for _ in range(workers + capacity):
                clients.enter_context(socket.create_connection(('127.0.0.1', port), timeout=5))
            def full():
                with vm.httpd.connection_lock:
                    return len(vm.httpd.connections) == workers + capacity and vm.httpd.pending.qsize() == capacity
            wait_for(full, 5)
            peak = mark()
            admitted = len(vm.httpd.connections)
            queued = vm.httpd.pending.qsize()
            status, body = fixture_http.request(port)
            calls_before_recovery = len(calls)
        def empty():
            with vm.httpd.connection_lock:
                return not vm.httpd.connections and vm.httpd.pending.empty()
        wait_for(empty, 5)
        recovery_status, recovery_body = fixture_http.request(port)
        return dict(admitted=admitted, queued=queued, overload=[status, body.decode()],
                    calls_before_recovery=calls_before_recovery, calls=len(calls),
                    recovery=[recovery_status, recovery_body.decode()], at_capacity=peak)


def cancellation_storm(config, iteration, mark):
    fixture = ParallelAdmission()
    groups, fan = config['storm_groups'], config['storm_arms_per_group']
    vm = fixture.make(fan=fan, limit=groups * fan, timeout=30000)
    original = vm.natives['core.time.sleep_ms']
    cancellations = [threading.Event() for _ in range(groups)]
    ready, lock = threading.Event(), threading.Lock()
    entered, outcomes = 0, []
    def sleeper(vm, args, func):
        nonlocal entered
        with lock:
            entered += 1
            if entered == groups * fan:
                ready.set()
        if not ready.wait(10):
            raise AssertionError('storm did not reach native entry')
        return original(vm, [30000], func)
    vm.natives['core.time.sleep_ms'] = sleeper
    def invoke(index):
        vm.cancels = (cancellations[index],)
        try:
            result = fixture.call(vm)
            outcomes.append(type(result).__name__)
        except Cancelled:
            outcomes.append('Cancelled')
        except Trap as error:
            outcomes.append('Trap' + str(error.code))
        except BaseException as error:
            outcomes.append(type(error).__name__ + ': ' + str(error))
    callers = [threading.Thread(target=invoke, args=(i,)) for i in range(groups)]
    try:
        for thread in callers:
            thread.start()
        if not ready.wait(10):
            raise AssertionError('storm admission watchdog')
        peak = mark()
        reserved = vm.parallel_budget.active
        started = time.perf_counter_ns()
        for event in cancellations:
            event.set()
        for thread in callers:
            thread.join(10)
        latency_ns = time.perf_counter_ns() - started
        if any(thread.is_alive() for thread in callers):
            raise AssertionError('storm caller survived cancellation')
        vm.natives['core.time.sleep_ms'] = original
        recovered = vm.run_parallel([vm.by_name['core.time.sleep_ms']], [0], 1, 1000)
        return dict(outcomes=sorted(outcomes), entered=entered, reserved=reserved,
                    active=vm.parallel_budget.active, rejected=vm.parallel_budget.rejected,
                    handoffs=len(vm.heap.handoffs), recovered=recovered == [UNIT],
                    cancellation_ns=latency_ns, at_capacity=peak)
    finally:
        for event in cancellations:
            event.set()
        for thread in callers:
            if thread.ident is not None:
                thread.join(10)
        fixture.doCleanups()


def lock_contention(config, iteration, mark):
    fixture = TransactionCancellation()
    fixture.setUp()
    vm, count = fixture.vm, config['lock_callers']
    kind = 'local' if iteration % 2 == 0 else 'process'
    events = [threading.Event() for _ in range(count)]
    expire, ready, seen_lock = threading.Event(), threading.Event(), threading.Lock()
    seen, indexes, outcomes = set(), {}, []
    original_remaining, original_flock = vm.db._lock_remaining, fcntl.flock
    def blocked():
        with seen_lock:
            seen.add(threading.get_ident())
            if len(seen) == count:
                ready.set()
    def remaining(deadline):
        index = indexes.get(threading.get_ident())
        if index is not None:
            if kind == 'local':
                blocked()
            if expire.is_set() and index % 2:
                vm.deadline_ns = 1
        return original_remaining(deadline)
    def flock(*args):
        try:
            return original_flock(*args)
        except BlockingIOError:
            blocked()
            raise
    if kind == 'local':
        vm.db._operation_lock.acquire()
        release = vm.db._operation_lock.release
    else:
        fd = os.open(Path(fixture.root, DIRECTORY, 'lock'), os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        release = lambda: os.close(fd)
    def invoke(index):
        indexes[threading.get_ident()] = index
        vm.cancels = (events[index],)
        try:
            outcomes.append(type(vm.call(fixture.ids['demo.apply'], [])).__name__)
        except Cancelled:
            outcomes.append('Cancelled')
        except Trap as error:
            outcomes.append('Trap' + str(error.code))
        except BaseException as error:
            outcomes.append(type(error).__name__ + ': ' + str(error))
    callers = [threading.Thread(target=invoke, args=(i,)) for i in range(count)]
    try:
        with patch.object(vm.db, '_lock_remaining', remaining), patch('gopyt.storage.fcntl.flock', flock):
            try:
                for thread in callers:
                    thread.start()
                if not ready.wait(10):
                    raise AssertionError('lock contention watchdog')
                peak = mark()
                started = time.perf_counter_ns()
                expire.set()
                for i, event in enumerate(events):
                    if i % 2 == 0:
                        event.set()
                for thread in callers:
                    thread.join(10)
                latency_ns = time.perf_counter_ns() - started
                all_stopped_while_held = not any(thread.is_alive() for thread in callers)
            finally:
                for event in events:
                    event.set()
                release()
                for thread in callers:
                    if thread.ident is not None:
                        thread.join(10)
        before_recovery = rows(fixture.root)
        recovered = vm.call(fixture.ids['demo.apply'], [])
        after_recovery = rows(fixture.root)
        return dict(kind=kind, blocked=len(seen), outcomes=sorted(outcomes),
                    stopped_before_release=all_stopped_while_held,
                    before_recovery=before_recovery, recovered=recovered is True,
                    after_recovery=after_recovery,
                    pending=len(list(Path(fixture.root, DIRECTORY).glob('.pending-*'))),
                    cancellation_ns=latency_ns, at_capacity=peak)
    finally:
        fixture.doCleanups()


COMMIT_FILES = {
    'spec/api.gopyt': '''module api

use core.status { DbError, ListenError }

http {
    get "/commit/{key}" commit
}

task commit(key: str) -> unit | DbError
    effects { database.write }

task serve() -> unit | ListenError
    effects { network, database.write }
''',
    'impl/api.gopyt': '''module api

use core.status { DbError, ListenError }
use net.http { serve }
use store.db { put }

task commit(key: str) -> unit | DbError
    effects { database.write }
{
    return store.db.put(key, "committed")
}

task serve() -> unit | ListenError
    effects { network, database.write }
{
    return net.http.serve()
}
''',
}


def shutdown_write(config, iteration, mark):
    entered, release = threading.Event(), threading.Event()
    original = os.replace
    publications = []
    def replacing(source, destination, *args, **kwargs):
        if destination == DATABASE:
            entered.set()
            if not release.wait(10):
                raise AssertionError('publication release watchdog')
            publications.append(destination)
        return original(source, destination, *args, **kwargs)
    with patch.object(fixture_http.fixtures, 'API_FILES', COMMIT_FILES):
        with fixture_http.running_server(MAX_HANDLERS=1, QUEUE=1) as (vm, port):
            with patch('gopyt.storage.os.replace', replacing):
                with socket.create_connection(('127.0.0.1', port), timeout=10) as client:
                    client.sendall(b'GET /commit/first HTTP/1.1\r\nHost: localhost\r\n\r\n'
                                   b'GET /commit/second HTTP/1.1\r\nHost: localhost\r\n\r\n')
                    try:
                        if not entered.wait(10):
                            raise AssertionError('compiled write did not reach publication')
                        peak = mark()
                        started = time.perf_counter_ns()
                        vm.httpd.shutdown()
                    finally:
                        release.set()
                    response = http.client.HTTPResponse(client)
                    response.begin()
                    observed = [response.status, response.read().decode()]
                    closed = client.recv(1) == b''
                    latency_ns = time.perf_counter_ns() - started
            observed_rows = rows(vm.root)
            pending = len(list(Path(vm.root, DIRECTORY).glob('.pending-*')))
        workers = len(vm.httpd.workers)
    return dict(response=observed, closed=closed, rows=observed_rows,
                publications=len(publications), pending=pending, workers=workers,
                drain_ns=latency_ns, at_publication=peak)
