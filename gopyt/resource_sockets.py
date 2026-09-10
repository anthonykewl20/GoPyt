"""Socket descriptor admission tied to physical close, including makefile readers."""
import socket
import threading

from gopyt.resource_control import ResourceClosedError


class _Socket(socket.socket):
    def _real_close(self):
        self._resource_owner.physical_close(lambda: super(_Socket, self)._real_close())

    def accept(self):
        return accept_socket(self._resource_owner.registry, self)

    def detach(self):
        raise ResourceClosedError('budgeted socket transfer requires an explicit owner')


class SocketOwner:
    def __init__(self, registry):
        self.registry = registry
        self.lock = threading.Lock()
        self.reservation = None
        self.sock = None
        self.state = 'opening'
        self.error = None
        self.pending_fd = None

    def physical_close(self, closer):
        with self.lock:
            if self.state == 'closed':
                return
            if self.state in ('closing', 'quarantined'):
                raise OSError('socket cleanup incomplete')
            self.state = 'closing'
        try:
            closer()
            self.reservation.release()
        except BaseException as error:
            with self.lock:
                self.error = type(error).__name__
                self.state = 'quarantined'
            raise
        with self.lock:
            self.state = 'closed'
            self.sock = None
        self.registry._remove(self)

    def close(self):
        with self.lock:
            if self.state == 'closed':
                return True
            if self.state != 'open':
                return False
            sock = self.sock
        try:
            sock.close()
        except BaseException:
            return False
        with self.lock:
            return self.state == 'closed'


def _acquire_socket(registry, initialize):
    owner = SocketOwner(registry)
    owner.reservation = registry._budget.reserve(descriptors=1)
    try:
        with registry._lock:
            if registry._closed:
                raise ResourceClosedError('descriptor registry is closed')
            registry._owners[id(owner)] = owner
    except BaseException:
        owner.reservation.release()
        raise
    try:
        # Allocate the Python shell and attach ownership before acquiring an FD.
        sock = _Socket.__new__(_Socket)
        sock._resource_owner = owner
        owner.sock = sock
        initialize(sock, owner)
    except BaseException:
        # Failed socket construction acquires no returned descriptor. If a
        # partially initialized socket has one, close it through its owner.
        if owner.sock is not None and owner.sock.fileno() >= 0:
            with owner.lock:
                owner.state = 'open'
            owner.close()
        else:
            pending, owner.pending_fd = owner.pending_fd, None
            try:
                owner.physical_close(lambda: socket.close(pending) if pending is not None else None)
            except BaseException:
                pass  # Preserve acquisition failure; cleanup remains quarantined.
        raise
    with owner.lock:
        owner.state = 'open'
    with registry._lock:
        closed = registry._closed
    if closed:
        owner.close()
        raise ResourceClosedError('descriptor registry closed during acquisition')
    return sock


def open_socket(registry, family=socket.AF_INET, kind=socket.SOCK_STREAM, protocol=0):
    return _acquire_socket(registry, lambda sock, owner:
                           socket.socket.__init__(sock, family, kind, protocol))


def accept_socket(registry, listener):
    address = None
    def initialize(sock, owner):
        nonlocal address
        # The shell and reservation already exist before the OS accept call.
        fd, address = listener._accept()
        owner.pending_fd = fd
        socket.socket.__init__(sock, listener.family, listener.type,
                               listener.proto, fileno=fd)
        owner.pending_fd = None
        if socket.getdefaulttimeout() is None and listener.gettimeout():
            sock.setblocking(True)
    return _acquire_socket(registry, initialize), address
