"""Local HTTP refund ledger; no external payment is performed.

Policy is GoPyT; transport, durable transactions, and idempotency are a trusted
Python adapter. Run with an independently pinned bundle. Bind loopback only.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import logging
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import sqlite3
import tempfile
import threading

from gopyt.guard import (canonical, compile_policy, digest, evaluate, materialize,
                         snapshot, timed_call)

MAX_MONEY = 10**12
MAX_VERSION = 10**9


class Rejected(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise Rejected(400, 'integer outside supported range')
    return value


def identifier(value):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value):
        raise Rejected(400, 'invalid identifier')
    return value


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('nonfinite JSON')
    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    if type(result) is not dict:
        raise ValueError('object required')
    return result


class TransactionQueue:
    """FIFO within one process; SQLite still arbitrates other processes."""
    def __init__(self):
        self.condition = threading.Condition()
        self.next_ticket = 0
        self.serving = 0

    @contextmanager
    def enter(self):
        with self.condition:
            ticket = self.next_ticket
            self.next_ticket += 1
            self.condition.wait_for(lambda: self.serving == ticket)
        try:
            yield
        finally:
            with self.condition:
                self.serving += 1
                self.condition.notify_all()


class Ledger:
    def __init__(self, db, candidate, bundle, pin):
        trusted = json.loads(bundle)
        if digest(bundle) != pin or trusted.get('adapter_sha256') != digest(Path(__file__).read_bytes()):
            raise ValueError('trusted bundle or adapter hash mismatch')
        self.temp = tempfile.TemporaryDirectory(prefix='gopyt-refund-service-')
        try:
            root = Path(self.temp.name, 'policy')
            root.mkdir()
            materialize(snapshot(candidate), root)
            self.receipt = evaluate(bundle, pin, root)
            if not self.receipt['accepted']:
                raise ValueError('policy admission rejected: ' + str(self.receipt))
            self.vm, self.ids = compile_policy(root)
        except BaseException:
            self.temp.cleanup()
            raise
        self.db = str(db)
        self.lock = threading.Lock()
        self.transactions = TransactionQueue()
        from contextlib import closing
        with closing(self.connect()) as conn:
            conn.executescript('''
            CREATE TABLE IF NOT EXISTS orders (
              id TEXT PRIMARY KEY, customer TEXT NOT NULL,
              paid INTEGER NOT NULL CHECK(paid >= 0 AND paid <= 1000000000000),
              refunded INTEGER NOT NULL DEFAULT 0 CHECK(refunded >= 0 AND refunded <= paid),
              version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1 AND version <= 1000000000),
              closed INTEGER NOT NULL DEFAULT 0 CHECK(closed IN (0,1)));
            CREATE TABLE IF NOT EXISTS requests (
              order_id TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL,
              response TEXT NOT NULL, PRIMARY KEY(order_id,key));
            ''')

    def connect(self):
        conn = sqlite3.connect(self.db, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA synchronous=FULL')
        return conn

    def close(self):
        self.temp.cleanup()

    def seed(self, orders):
        """Trusted offline import only. HTTP clients cannot set paid balances."""
        prepared = []
        for row in orders:
            if set(row) != {'id', 'customer', 'paid'}:
                raise ValueError('invalid import fields')
            prepared.append((identifier(row['id']), identifier(row['customer']),
                             integer(row['paid'], 0, MAX_MONEY)))
        conn = self.connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            conn.executemany('INSERT INTO orders(id,customer,paid) VALUES(?,?,?)', prepared)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def apply(self, body):
        with self.transactions.enter():
            return self._apply(body)

    def _apply(self, body):
        if set(body) != {'order_id', 'customer_id', 'amount', 'expected_version', 'idempotency_key'}:
            raise Rejected(400, 'exact request fields required')
        order_id = identifier(body['order_id'])
        customer = identifier(body['customer_id'])
        key = identifier(body['idempotency_key'])
        amount = integer(body['amount'], 1, MAX_MONEY)
        expected = integer(body['expected_version'], 1, MAX_VERSION - 1)
        payload = canonical(body).decode()
        # A transaction covers decision, ledger update, and replay receipt.
        conn = self.connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
            if row is None or row['customer'] != customer:
                raise Rejected(404, 'order not found for customer')
            old = conn.execute('SELECT * FROM requests WHERE order_id=? AND key=?', (order_id,key)).fetchone()
            if old:
                if old['payload'] != payload:
                    raise Rejected(409, 'idempotency key reused with different request')
                conn.commit()
                return json.loads(old['response'])
            with self.lock:
                allowed = timed_call(self.vm, self.ids['refund_policy.allowed'],
                                     [row['paid'],row['refunded'],amount,row['version'],expected,bool(row['closed'])])
                if allowed is not True:
                    raise Rejected(409, 'refund policy rejected')
                after = timed_call(self.vm, self.ids['refund_policy.after_refund'],
                                   [row['paid'],row['refunded'],amount])
            response = {'order_id': order_id, 'customer_id': customer,
                        'refunded': after, 'remaining': row['paid'] - after,
                        'version': row['version'] + 1, 'currency': 'GBP',
                        'policy_sha256': self.receipt['candidate_sha256'],
                        'bundle_sha256': self.receipt['bundle_sha256']}
            conn.execute('UPDATE orders SET refunded=?,version=version+1 WHERE id=?', (after,order_id))
            conn.execute('INSERT INTO requests VALUES(?,?,?,?)', (order_id,key,payload,canonical(response).decode()))
            conn.commit()
            return response
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128
    def __init__(self, ledger, credentials, port=0):
        if not credentials or any(type(k) is not str or len(k) < 32 for k in credentials):
            raise ValueError('trusted customer tokens must have at least 32 characters')
        self.credentials = {digest(k.encode()): identifier(v) for k,v in credentials.items()}
        self.ledger = ledger
        super().__init__(('127.0.0.1', port), Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass

    def do_POST(self):
        status, result = 200, None
        try:
            if self.path != '/refunds':
                raise Rejected(404, 'unknown route')
            auth = self.headers.get_all('Authorization', [])
            if len(auth) != 1 or not auth[0].startswith('Bearer '):
                raise Rejected(401, 'bearer credential required')
            principal = self.server.credentials.get(digest(auth[0][7:].encode()))
            if principal is None:
                raise Rejected(401, 'invalid credential')
            lengths = self.headers.get_all('Content-Length', [])
            if (len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal()
                    or self.headers.get('Transfer-Encoding') is not None):
                raise Rejected(400, 'one content length required; transfer encoding unsupported')
            length = int(lengths[0])
            if not 1 <= length <= 4096:
                raise Rejected(413, 'request too large')
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                raise Rejected(415, 'application/json required')
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise Rejected(400, 'truncated request')
            body = strict_json(raw)
            if body.get('customer_id') != principal:
                raise Rejected(403, 'customer does not match authenticated principal')
            result = self.server.ledger.apply(body)
        except Rejected as exc:
            status, result = exc.status, {'error': exc.message}
        except (ValueError, UnicodeError, RecursionError):
            status, result = 400, {'error': 'invalid JSON'}
        except Exception:
            logging.exception('Refund transaction failed')
            status, result = 503, {'error': 'operation failed without committing a refund'}
        raw = canonical(result)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--db', required=True)
    parser.add_argument('--credentials', required=True, help='Trusted JSON token-to-customer map; keep secret and outside candidate')
    parser.add_argument('--seed', help='Trusted offline JSON array; only on a fresh database')
    parser.add_argument('--port', type=int, default=8085)
    args = parser.parse_args()
    ledger = Ledger(args.db, args.candidate, Path(args.bundle).read_bytes(), args.expected_sha256)
    try:
        if args.seed:
            ledger.seed(json.loads(Path(args.seed).read_text()))
        with Server(ledger, json.loads(Path(args.credentials).read_text()), args.port) as server:
            print('Local refund ledger: http://127.0.0.1:' + str(server.server_port), flush=True)
            server.serve_forever()
    finally:
        ledger.close()


if __name__ == '__main__':
    main()
