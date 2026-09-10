"""Elapsed-time budgets around standard-library HTTP parsing and socket I/O."""
from functools import partial
import http.client
import io
import socket
import time
import urllib.request
from gopyt.net_policy import AddressPolicyError, check_address
from gopyt.resource_sockets import open_socket, wrap_tls


class Budget:
    def __init__(self, vm, timeout_ms: int):
        self.vm = vm
        self.deadline = time.monotonic_ns() + timeout_ms * 1_000_000

    def remaining(self) -> float:
        self.vm.check_cancelled()
        deadline = self.deadline
        if self.vm.deadline_ns is not None:
            deadline = min(deadline, self.vm.deadline_ns)
        left = deadline - time.monotonic_ns()
        if left <= 0:
            raise TimeoutError('HTTP elapsed-time budget expired')
        return left / 1_000_000_000


class _Reader(io.RawIOBase):
    def __init__(self, sock, budget):
        self.sock = sock
        self.budget = budget
        # Retain the descriptor when HTTPConnection closes a connection-close
        # response. Read directly from the socket so a polling timeout cannot
        # poison SocketIO's buffered-reader state.
        self.owner = None
        self.owner = sock.makefile('rb', buffering=0)

    def readable(self):
        return True

    def readinto(self, buffer):
        if not buffer:
            return 0
        while True:
            self.sock.settimeout(min(.05, self.budget.remaining()))
            try:
                return self.sock.recv_into(buffer)
            except TimeoutError:
                # No application bytes were returned; retain parser/SSL state.
                self.budget.remaining()

    def close(self):
        if not self.closed:
            try:
                if self.owner is not None:
                    self.owner.close()
            finally:
                super().close()


class _Response(http.client.HTTPResponse):
    def __init__(self, sock, *args, budget, **kwargs):
        super().__init__(sock, *args, **kwargs)
        try:
            reader = _Reader(sock, budget)
        except BaseException:
            self.close()
            raise
        try:
            self.fp.close()
            self.fp = io.BufferedReader(reader)
        except BaseException:
            reader.close()
            raise


class _Connection:
    def __init__(self, host, *, budget, **kwargs):
        self.budget = budget
        self.connected_address = None
        super().__init__(host, **kwargs)
        self.response_class = partial(_Response, budget=budget)

    def connect(self):
        if self._tunnel_host:
            raise OSError('HTTP proxy tunnels are disabled')
        self.budget.remaining()
        # Host DNS is not interruptible here. Its elapsed time still counts,
        # and no connection attempt starts if resolution exhausts the budget.
        addresses = socket.getaddrinfo(self.host, self.port, 0, socket.SOCK_STREAM)
        failure = None
        refused = None
        for family, kind, protocol, _name, address in addresses:
            self.budget.remaining()
            # An allowlisted origin names a host, not an address. Check what the
            # resolver actually returned before any socket exists, so a name
            # pointed at loopback, private or link-local space never connects.
            try:
                check_address(self.host, address[0])
            except AddressPolicyError as error:
                refused = error
                continue
            self.connected_address = address[0]
            sock = open_socket(self.budget.vm.descriptors, family, kind, protocol)
            try:
                sock.settimeout(self.budget.remaining())
                if self.source_address:
                    sock.bind(self.source_address)
                sock.connect(address)
                self.budget.remaining()
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except BaseException as error:
                sock.close()
                if not isinstance(error, OSError):
                    raise
                failure = error
                continue
            self.sock = sock
            return
        self.budget.remaining()
        if failure is None and refused is not None:
            raise refused
        raise failure or OSError('DNS returned no stream addresses')

    def send(self, data):
        if self.sock is None:
            self.connect()
        # Native requests provide bytes, never host file objects/iterators.
        with memoryview(data) as view:
            for start in range(0, view.nbytes, 65_536):
                self.sock.settimeout(self.budget.remaining())
                self.sock.sendall(view[start:start + 65_536])
        self.budget.remaining()


class _HTTPConnection(_Connection, http.client.HTTPConnection):
    pass


class _HTTPSConnection(_Connection, http.client.HTTPSConnection):
    def connect(self):
        super().connect()
        try:
            self.sock.settimeout(self.budget.remaining())
            self.sock = wrap_tls(self.sock, self._context, server_hostname=self.host)
            self.budget.remaining()
        except BaseException:
            self.close()
            raise


class _HTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, budget):
        super().__init__()
        self.budget = budget

    def http_open(self, request):
        return self.do_open(partial(_HTTPConnection, budget=self.budget), request)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, budget):
        super().__init__()
        self.budget = budget

    def https_open(self, request):
        return self.do_open(partial(_HTTPSConnection, budget=self.budget), request)


def opener(budget, redirect_handler):
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), redirect_handler,
                                       _HTTPHandler(budget), _HTTPSHandler(budget))
