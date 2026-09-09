"""`task serve` (docs/implementer.md 12).

Routes come from the artifact's route table; there is no handler list value and
no middleware. Status is 200 with the JSON of the return value, or 404/400/500/
503/504/413 with an empty body, exactly as the document fixes them.
"""

from __future__ import annotations

import os
import secrets
import io
import queue
import signal
import socket
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gopyt import jsonc, ops
from gopyt.jsonc import ConvertFail, NotJson
from gopyt.values import UNIT, Unit
from gopyt.vm import Trap, Cancelled

DEFAULT_ADDR = ("127.0.0.1", 8080)
MAX_REQUEST_BODY = 1_048_576
MAX_HANDLERS = 64
QUEUE = 1024
REQUEST_TIMEOUT_SECONDS = 10.0
TCP_NODELAY = True


class _WorkerStartError(Exception):
    """Handler pool could not be started; its resources have been reclaimed."""


class _DeadlineReader(io.RawIOBase):
    """Bound total request input time, including clients sending one byte at a time."""

    def __init__(self, connection, deadline):
        self.connection = connection
        self.deadline = deadline

    def readable(self):
        return True

    def readinto(self, buffer):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("request deadline")
        self.connection.settimeout(remaining)
        return self.connection.recv_into(buffer)


def _addr() -> tuple[str, int] | None:
    raw = os.environ.get("GOPYT_HTTP_ADDR")
    if not raw:
        return DEFAULT_ADDR
    if ":" not in raw:
        return None
    host, port = raw.rsplit(":", 1)
    significant = port.lstrip("0") or "0"
    if len(significant) > 5 or not port.isascii() or not port.isdigit() or not (1 <= int(significant) <= 65535) or not host:
        return None
    return (host, int(significant))


def _match(pattern: str, path: str) -> dict[str, str] | None:
    p_segs = pattern.split("/")
    a_segs = path.split("/")
    if len(p_segs) != len(a_segs):
        return None
    binds: dict[str, str] = {}
    for want, got in zip(p_segs, a_segs):
        if want.startswith("{") and want.endswith("}"):
            if not got:
                return None
            binds[want[1:-1]] = got
        elif want != got:
            return None
    return binds


def serve(vm, module: str):
    from gopyt.natives import _status

    with vm.lock:
        # implementer.md 12: one outstanding serve per process.
        if vm.serving:
            return _status(vm, "ListenError", "already serving")
        vm.serving = True
    addr = _addr()
    if addr is None:
        vm.serving = False
        return _status(vm, "ListenError", "address")
    if not vm.allows_resources(('listen', f'{addr[0]}:{addr[1]}')):
        vm.serving = False
        return _status(vm, "ListenError", "resource authority denied")
    authority = vm.authority
    identities = vm.identities
    if identities is not None and (identities.module != module
            or not identities.authority.is_descendant_of(authority)):
        vm.serving = False
        return _status(vm, "ListenError", "identity broker audience or serving authority mismatch")
    from gopyt.security_config import http_token, SecurityError
    try:
        authorization = http_token(vm.root, addr, session_auth=identities is not None)
    except SecurityError:
        vm.serving = False
        return _status(vm, "ListenError", "security configuration")
    routes = []
    for r in vm.routes_for(module):
        path = vm.art.const_str(r.path)
        names = [vm.art.const_str(ix) for ix in r.params]
        placeholders = [s[1:-1] for s in path.split("/") if s.startswith("{") and s.endswith("}")]
        routes.append((r.method, path, r.handler, (names, placeholders)))
    if not routes:
        vm.serving = False
        return _status(vm, "ListenError", "no routes")
    stop = threading.Event()
    inherited_cancels = vm.cancels
    inherited_deadline = vm.deadline_ns

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = TCP_NODELAY

        def setup(self):
            super().setup()
            self.rfile.close()
            # Keep the deadline on the reader: a closure capturing this handler
            # would retain its headers and buffers until Python's cyclic GC.
            self.rfile = io.BufferedReader(_DeadlineReader(
                self.connection, time.monotonic() + REQUEST_TIMEOUT_SECONDS))

        def handle_one_request(self):
            self.rfile.raw.deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
            super().handle_one_request()

        def handle(self):
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, Cancelled):
                self.close_connection = True

        def log_message(self, *a):  # no request log in v0
            return

        def _empty(self, status: int) -> None:
            if status in (400, 401, 403, 404, 413, 503):
                vm.observe.http(getattr(self, "route_tag", "unmatched"), str(status), 0.0)
            self.send_response(status)
            self.send_header("Content-Length", "0")
            if status == 401:
                self.send_header("WWW-Authenticate", 'Bearer realm="gopyt"')
            self.end_headers()

        def handle_one(self, method_num: int) -> None:
            self.route_tag = "unmatched"
            session = None
            if identities is not None:
                headers = self.headers.get_all("Authorization", [])
                session = identities.authenticate(headers[0]) if len(headers) == 1 else None
                if session is None:
                    self.close_connection = True
                    self._empty(401)
                    return
            if authorization is not None:
                headers = self.headers.get_all("Authorization", [])
                try:
                    supplied = headers[0].encode('ascii') if len(headers) == 1 else b''
                except UnicodeError:
                    supplied = b''
                if not secrets.compare_digest(supplied, authorization):
                    self.close_connection = True
                    self._empty(401)
                    return
            path = self.path.split("?", 1)[0]
            lengths = self.headers.get_all("Content-Length", [])
            raw_length = lengths[0] if lengths else "0"
            if (len(lengths) > 1 or not raw_length.isascii() or not raw_length.isdigit()
                    or self.headers.get("Transfer-Encoding") is not None):
                self.close_connection = True
                self._empty(400)
                return
            significant = raw_length.lstrip("0") or "0"
            length = MAX_REQUEST_BODY + 1 if len(significant) > 7 else int(significant)
            if length > MAX_REQUEST_BODY:
                self.close_connection = True
                self._empty(413)
                return
            body = self.rfile.read(length) if length else b""
            if len(body) != length:
                self.close_connection = True
                self._empty(400)
                return
            self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)
            hit = None
            for m, pattern, fn_id, meta in routes:
                if m != method_num:
                    continue
                binds = _match(pattern, path)
                if binds is not None:
                    hit = (fn_id, meta, binds)
                    self.route_tag = pattern
                    break
            if hit is None:
                self._empty(404)
                return
            fn_id, (names, placeholders), binds = hit
            if session is not None:
                if (self.command, self.route_tag) not in session.routes:
                    self._empty(403)
                    return
                if not session.authority.admits(()):
                    self.close_connection = True
                    self._empty(401)
                    return
                with vm.request_scope(session):
                    self._dispatch(fn_id, names, placeholders, binds, body, method_num)
            else:
                self._dispatch(fn_id, names, placeholders, binds, body, method_num)

        def _dispatch(self, fn_id, names, placeholders, binds, body, method_num) -> None:
            import time as _time

            started = _time.monotonic()
            func = vm.art.funcs[fn_id]
            args = []
            with vm.heap.pin(args):
                try:
                    for i, name in enumerate(names):
                        te = func.params[i]
                        if name in placeholders:
                            value = _from_str(vm, binds[name], te)
                            if value is _BAD:
                                self._empty(400)
                                return
                            args.append(value)
                        else:
                            ctype = self.headers.get("Content-Type")
                            if ctype is not None and ctype != "application/json":
                                self._empty(400)
                                return
                            try:
                                args.append(jsonc.decode(vm.art, body.decode("utf-8"), te))
                            except (ConvertFail, NotJson, UnicodeDecodeError):
                                self._empty(400)
                                return
                    if len(names) == len(placeholders) and body:
                        if method_num in (2, 3, 4):
                            self._empty(400)
                            return
                    try:
                        vm.cancels = (stop,)
                        result = vm.call(fn_id, args)
                    except Cancelled:
                        self.close_connection = True
                        return
                    except Trap as error:
                        status = (503 if error.code == ops.TRAP_PAR_MAX else
                                  504 if error.code == ops.TRAP_TIMEOUT else 500)
                        if status != 503:  # _empty records overload admission itself.
                            vm.observe.http(self.route_tag, str(status), (_time.monotonic() - started) * 1000.0)
                        self._empty(status)
                        return
                    if isinstance(result, Unit):
                        vm.observe.http(self.route_tag, "ok", (_time.monotonic() - started) * 1000.0)
                        self._empty(200)
                        return
                    try:
                        text = jsonc.encode(vm.art, result, func.ret)
                    except (ConvertFail, NotJson):
                        vm.observe.http(self.route_tag, "ConvertError", (_time.monotonic() - started) * 1000.0)
                        self._empty(500)
                        return
                    raw = text.encode("utf-8")
                    vm.observe.http(self.route_tag, "ok", (_time.monotonic() - started) * 1000.0)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)

                finally:
                    vm.heap.release_result()

        def do_GET(self):
            self.handle_one(1)

        def do_POST(self):
            self.handle_one(2)

        def do_PUT(self):
            self.handle_one(3)

        def do_PATCH(self):
            self.handle_one(4)

        def do_DELETE(self):
            self.handle_one(5)

    class Server(ThreadingHTTPServer):
        request_queue_size = QUEUE

        def server_bind(self):
            # Routing uses declared paths, not the machine's reverse-DNS name.
            # HTTPServer.server_bind performs an unbounded getfqdn lookup.
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = self.server_address[:2]

        def __init__(self, *args):
            super().__init__(*args)
            try:
                self.pending = queue.Queue(maxsize=QUEUE)
                self.connections = set()
                self.connection_lock = threading.Lock()
                self.workers = []
                for _ in range(MAX_HANDLERS):
                    worker = threading.Thread(target=self.worker, daemon=True)
                    self.workers.append(worker)
                    try:
                        worker.start()
                    except (RuntimeError, OSError) as error:
                        raise _WorkerStartError() from error
            except BaseException:
                self.server_close()
                raise

        def service_actions(self):
            # Runs in the serving coordinator; raising leaves serve_forever's
            # finally block intact and avoids shutdown()'s same-thread deadlock.
            vm.check_cancelled()

        def process_request(self, request, address):
            try:
                vm.check_cancelled()
            except (Cancelled, Trap):
                self.shutdown_request(request)
                return  # service_actions reports the context exit to the caller.
            with self.connection_lock:
                self.connections.add(request)
            try:
                self.pending.put_nowait((request, address))
            except queue.Full:
                try:
                    request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    vm.observe.http("unmatched", "503", 0.0)
                finally:
                    self.shutdown_request(request)
                    with self.connection_lock:
                        self.connections.discard(request)

        def worker(self):
            vm._tl.authority = authority
            vm.cancels = inherited_cancels + (stop,)
            vm.deadline_ns = inherited_deadline
            while True:
                item = self.pending.get()
                try:
                    if item is None:
                        return
                    request, address = item
                    if stop.is_set():
                        self.shutdown_request(request)
                    else:
                        self.process_request_thread(request, address)
                    with self.connection_lock:
                        self.connections.discard(request)
                finally:
                    vm.heap.release_result()
                    self.pending.task_done()

        def server_close(self):
            stop.set()
            if hasattr(self, "workers"):
                with self.connection_lock:
                    for connection in self.connections:
                        try:
                            connection.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                started = [worker for worker in self.workers if worker.ident is not None]
                for _worker in started:
                    self.pending.put(None)
                for worker in started:
                    worker.join()
                self.workers.clear()
            super().server_close()

    try:
        httpd = Server(addr, Handler)
    except _WorkerStartError:
        vm.serving = False
        return _status(vm, "ListenError", "worker startup")
    except OSError:
        vm.serving = False
        return _status(vm, "ListenError", "bind")
    except BaseException:
        vm.serving = False
        raise
    vm.httpd = httpd  # SIGINT/SIGTERM and in-process callers stop it here

    def shutdown(_signum, _frame):
        stop.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    previous_signals = {}
    try:
        previous_signals = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)
    except ValueError:
        previous_signals = {}
    try:
        httpd.serve_forever(poll_interval=0.1)
    finally:
        httpd.server_close()
        for sig, handler in previous_signals.items():
            signal.signal(sig, handler)
        vm.serving = False
    return UNIT


_BAD = object()


def _from_str(vm, text: str, te_ix: int):
    te = vm.art.texprs[te_ix]
    if te.tag == 7:  # str
        return text
    name = None
    if te.tag == 13:
        name = vm.art.const_str(vm.art.types[te.a].name)
    else:
        name = {1: "bool", 3: "i64"}.get(te.tag)
    if name is None:
        return _BAD
    fn_id = vm.by_name.get(f"provide:core.convert.FromStr:{name}:from_str")
    if fn_id is None:
        return _BAD
    value = vm.call(fn_id, [text])
    from gopyt.values import Record

    if isinstance(value, Record) and vm.type_name(value.type_id) == "core.status.ConvertError":
        return _BAD
    return value
