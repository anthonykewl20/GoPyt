"""The trusted-gateway boundary, header bounds and identity-aware admission.

These run through real sockets, including a real TLS terminator in front of the
loopback service, because the boundary this issue is about only exists on a
deployed network path.
"""
import contextlib
import datetime
import http.client
import ipaddress
import os
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from gopyt.gateway import GatewayPolicy, from_environment
from gopyt.security_config import SecurityError
from gopyt.test_app_runtime import running_server
import gopyt.server as server


def _certificate(directory):
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
    except ImportError:  # pragma: no cover - exercised only without the extra
        raise unittest.SkipTest('cryptography extra required for the TLS gateway')
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'gateway')])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - datetime.timedelta(days=1))
                   .not_valid_after(now + datetime.timedelta(days=1))
                   .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                   .add_extension(x509.SubjectAlternativeName(
                       [x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
                   .sign(key, hashes.SHA256()))
    cert_path, key_path = Path(directory) / 'gateway.pem', Path(directory) / 'gateway.key'
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    key_path.chmod(0o600)
    return cert_path, key_path


class _Gateway(threading.Thread):
    """A TLS terminator that forwards to the loopback service.

    It is deliberately small: accept TLS, add the forwarded identity header the
    service is configured to believe, and relay bytes. Its point is that the
    service is reached only over a real network path it did not terminate.
    """

    def __init__(self, upstream_port, cert, key, *, identity='alice',
                 header='X-GoPyT-Client', pass_through=None):
        super().__init__(daemon=True)
        self.upstream_port = upstream_port
        self.identity = identity
        self.header = header
        self.pass_through = pass_through
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(cert, key)
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(8)
        self.port = self.listener.getsockname()[1]
        self.running = True
        self.failures = []

    def run(self):
        while self.running:
            try:
                raw, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(raw,), daemon=True).start()

    def _serve(self, raw):
        try:
            with self.context.wrap_socket(raw, server_side=True) as client:
                client.settimeout(5)
                request = b''
                while b'\r\n\r\n' not in request:
                    chunk = client.recv(4096)
                    if not chunk:
                        return
                    request += chunk
                head, _, rest = request.partition(b'\r\n\r\n')
                lines = head.split(b'\r\n')
                injected = list(lines[:1])
                for line in lines[1:]:
                    # The gateway owns this header: a client's own copy is
                    # dropped here as well as disbelieved downstream.
                    if not line.lower().startswith(self.header.lower().encode() + b':'):
                        injected.append(line)
                if self.pass_through is not None:
                    injected.extend(self.pass_through)
                elif self.identity is not None:
                    injected.append(self.header.encode() + b': ' + self.identity.encode())
                forwarded = b'\r\n'.join(injected) + b'\r\n\r\n' + rest
                with socket.create_connection(('127.0.0.1', self.upstream_port), timeout=5) as up:
                    up.sendall(forwarded)
                    up.settimeout(5)
                    while True:
                        data = up.recv(65536)
                        if not data:
                            return
                        client.sendall(data)
        except (OSError, ssl.SSLError) as error:
            self.failures.append(error)

    def stop(self):
        self.running = False
        with contextlib.suppress(OSError):
            # Unblock a thread parked in accept before closing the descriptor,
            # so the port really stops listening rather than staying open.
            self.listener.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            self.listener.close()
        self.join(2)


@contextlib.contextmanager
def _environment(**values):
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class GatewayConfiguration(unittest.TestCase):
    def test_a_forwarded_header_without_a_gateway_is_a_refused_configuration(self):
        with self.assertRaises(SecurityError):
            from_environment({'GOPYT_HTTP_FORWARDED_HEADER': 'X-Client'})
        with self.assertRaises(SecurityError):
            from_environment({'GOPYT_HTTP_FORWARDED_REQUIRED': '1'})

    def test_malformed_configuration_fails_closed(self):
        for environment in ({'GOPYT_HTTP_GATEWAY_ADDR': 'gateway.internal'},
                            {'GOPYT_HTTP_GATEWAY_ADDR': '127.0.0.1',
                             'GOPYT_HTTP_FORWARDED_HEADER': 'bad header'},
                            {'GOPYT_HTTP_GATEWAY_ADDR': '127.0.0.1',
                             'GOPYT_HTTP_FORWARDED_REQUIRED': 'yes'},
                            {'GOPYT_HTTP_RATE': '5'},
                            {'GOPYT_HTTP_RATE': '0/1000'}):
            with self.subTest(environment=environment):
                with self.assertRaises(SecurityError):
                    from_environment(environment)

    def test_an_untrusted_peer_can_never_assert_an_identity(self):
        policy = GatewayPolicy(peer='127.0.0.1', required=False)
        self.assertEqual(policy.forwarded_identity('10.0.0.4', ['root']), (None, None))
        self.assertEqual(policy.forwarded_identity('127.0.0.1', ['root']), ('root', None))
        self.assertEqual(policy.forwarded_identity('127.0.0.1', ['a', 'b']), (None, 'duplicate'))
        self.assertEqual(policy.forwarded_identity('127.0.0.1', ['bad value']), (None, 'malformed'))
        self.assertEqual(policy.forwarded_identity('127.0.0.1', ['x' * 257]), (None, 'malformed'))

    def test_no_gateway_means_no_forwarded_identity_at_all(self):
        policy = from_environment({})
        self.assertFalse(policy.trusts_a_gateway)
        self.assertTrue(policy.accepts_peer('10.0.0.4'))
        self.assertEqual(policy.forwarded_identity('127.0.0.1', ['root']), (None, None))


class GatewayBoundary(unittest.TestCase):
    def request(self, port, path='/echo/abc', headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        try:
            connection.request('GET', path, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_only_the_configured_gateway_peer_is_served(self):
        with _environment(GOPYT_HTTP_GATEWAY_ADDR='10.99.99.99'):
            with running_server(MAX_HANDLERS=2) as (vm, port):
                # Every request here comes from loopback, not from the gateway.
                self.assertEqual(self.request(port), (403, b''))

    def test_a_client_supplied_forwarded_header_is_never_believed(self):
        # With no gateway configured there is nothing to believe, so two clients
        # asserting different identities must share one bucket keyed on the peer
        # rather than each getting their own allowance.
        with _environment(GOPYT_HTTP_GATEWAY_ADDR=None, GOPYT_HTTP_FORWARDED_HEADER=None,
                          GOPYT_HTTP_RATE='2/60000'):
            with running_server(MAX_HANDLERS=2) as (vm, port):
                self.assertEqual(self.request(
                    port, headers={'X-GoPyT-Client': 'alice'}), (200, b'{"amount":3}'))
                self.assertEqual(self.request(
                    port, headers={'X-GoPyT-Client': 'bob'}), (200, b'{"amount":3}'))
                self.assertEqual(self.request(
                    port, headers={'X-GoPyT-Client': 'carol'})[0], 429)

    def test_the_bounded_header_block_is_this_servers_own(self):
        with running_server(MAX_HANDLERS=2) as (vm, port):
            self.assertEqual(server.MAX_HEADER_COUNT, 64)
            self.assertEqual(self.request(
                port, headers={f'X-Pad-{index}': 'v' for index in range(80)})[0], 431)
            self.assertEqual(self.request(
                port, headers={'X-Pad': 'v' * (server.MAX_HEADER_LINE + 16)})[0], 431)
            padding = {f'X-Pad-{index}': 'v' * 1000 for index in range(40)}
            self.assertEqual(self.request(port, headers=padding)[0], 431)
            # A request within every bound still works on the same server.
            self.assertEqual(self.request(port), (200, b'{"amount":3}'))

    def test_an_oversized_request_line_is_refused_without_a_body(self):
        with running_server(MAX_HANDLERS=2) as (vm, port):
            with socket.create_connection(('127.0.0.1', port), timeout=3) as raw:
                raw.sendall(b'GET /echo/' + b'a' * (server.MAX_REQUEST_LINE + 32)
                            + b' HTTP/1.1\r\nHost: x\r\n\r\n')
                response = http.client.HTTPResponse(raw)
                response.begin()
                self.assertEqual((response.status, response.read()), (414, b''))

    def test_identity_aware_admission_limits_each_identity_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            cert, key = _certificate(directory)
            with _environment(GOPYT_HTTP_GATEWAY_ADDR='127.0.0.1',
                              GOPYT_HTTP_RATE='2/60000'):
                with running_server(MAX_HANDLERS=4) as (vm, port):
                    first = _Gateway(port, cert, key, identity='alice')
                    second = _Gateway(port, cert, key, identity='bob')
                    first.start(); second.start()
                    self.addCleanup(first.stop)
                    self.addCleanup(second.stop)
                    context = ssl.create_default_context(cafile=str(cert))
                    def through(gateway):
                        connection = http.client.HTTPSConnection(
                            '127.0.0.1', gateway.port, context=context, timeout=5)
                        try:
                            connection.request('GET', '/echo/abc')
                            response = connection.getresponse()
                            return response.status, response.read()
                        finally:
                            connection.close()
                    self.assertEqual(through(first), (200, b'{"amount":3}'))
                    self.assertEqual(through(first), (200, b'{"amount":3}'))
                    self.assertEqual(through(first)[0], 429)
                    # A different asserted identity has its own bucket.
                    self.assertEqual(through(second), (200, b'{"amount":3}'))

    def test_a_real_tls_gateway_reaches_the_service_and_its_failure_is_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            cert, key = _certificate(directory)
            with _environment(GOPYT_HTTP_GATEWAY_ADDR='127.0.0.1',
                              GOPYT_HTTP_FORWARDED_REQUIRED='1'):
                with running_server(MAX_HANDLERS=4) as (vm, port):
                    gateway = _Gateway(port, cert, key, identity='alice')
                    gateway.start()
                    self.addCleanup(gateway.stop)
                    context = ssl.create_default_context(cafile=str(cert))
                    connection = http.client.HTTPSConnection(
                        '127.0.0.1', gateway.port, context=context, timeout=5)
                    try:
                        connection.request('GET', '/echo/abc')
                        response = connection.getresponse()
                        self.assertEqual((response.status, response.read()),
                                         (200, b'{"amount":3}'))
                    finally:
                        connection.close()
                    # An untrusted certificate is refused by the client, and the
                    # service is never reached: TLS is the gateway's boundary.
                    strict = ssl.create_default_context()
                    with self.assertRaises(ssl.SSLError):
                        bad = http.client.HTTPSConnection(
                            '127.0.0.1', gateway.port, context=strict, timeout=5)
                        try:
                            bad.request('GET', '/echo/abc')
                            bad.getresponse()
                        finally:
                            bad.close()
                    # Gateway loss is a connection failure, not a served request.
                    gateway.stop()
                    time.sleep(.05)
                    with self.assertRaises(OSError):
                        lost = http.client.HTTPSConnection(
                            '127.0.0.1', gateway.port, context=context, timeout=2)
                        try:
                            lost.request('GET', '/echo/abc')
                            lost.getresponse()
                        finally:
                            lost.close()

    def test_a_required_identity_the_gateway_omits_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            cert, key = _certificate(directory)
            with _environment(GOPYT_HTTP_GATEWAY_ADDR='127.0.0.1',
                              GOPYT_HTTP_FORWARDED_REQUIRED='1'):
                with running_server(MAX_HANDLERS=2) as (vm, port):
                    gateway = _Gateway(port, cert, key, identity=None)
                    gateway.start()
                    self.addCleanup(gateway.stop)
                    context = ssl.create_default_context(cafile=str(cert))
                    connection = http.client.HTTPSConnection(
                        '127.0.0.1', gateway.port, context=context, timeout=5)
                    try:
                        connection.request('GET', '/echo/abc')
                        response = connection.getresponse()
                        self.assertEqual((response.status, response.read()), (401, b''))
                    finally:
                        connection.close()

    def test_the_service_can_restart_behind_a_live_gateway(self):
        """Deployment restarts the service, not the gateway in front of it."""
        with tempfile.TemporaryDirectory() as directory:
            cert, key = _certificate(directory)
            with _environment(GOPYT_HTTP_GATEWAY_ADDR='127.0.0.1'):
                with running_server(MAX_HANDLERS=2) as (vm, port):
                    gateway = _Gateway(port, cert, key, identity='alice')
                    gateway.start()
                    self.addCleanup(gateway.stop)
                    context = ssl.create_default_context(cafile=str(cert))
                    def through():
                        connection = http.client.HTTPSConnection(
                            '127.0.0.1', gateway.port, context=context, timeout=5)
                        try:
                            connection.request('GET', '/echo/abc')
                            response = connection.getresponse()
                            return response.status, response.read()
                        finally:
                            connection.close()
                    self.assertEqual(through(), (200, b'{"amount":3}'))
                # The service is down; the gateway is still listening, so the
                # client reaches the gateway and the gateway fails upstream.
                with self.assertRaises((OSError, http.client.HTTPException)):
                    through()
                # The gateway process, its certificate and the client's trust
                # in it all survive the restart; only its upstream moves, the
                # way a deployment's own configuration reload would move it.
                with running_server(MAX_HANDLERS=2) as (restarted, again):
                    gateway.upstream_port = again
                    self.assertEqual(through(), (200, b'{"amount":3}'))
        
    def test_connection_exhaustion_through_the_gateway_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            cert, key = _certificate(directory)
            with _environment(GOPYT_HTTP_GATEWAY_ADDR='127.0.0.1'):
                with running_server(MAX_HANDLERS=1, QUEUE=1) as (vm, port):
                    gateway = _Gateway(port, cert, key, identity='alice')
                    gateway.start()
                    self.addCleanup(gateway.stop)
                    context = ssl.create_default_context(cafile=str(cert))
                    held = []
                    try:
                        for _ in range(6):
                            raw = socket.create_connection(('127.0.0.1', gateway.port),
                                                           timeout=3)
                            held.append(context.wrap_socket(
                                raw, server_hostname='127.0.0.1'))
                    except (OSError, ssl.SSLError):
                        pass  # saturation may refuse the handshake itself
                    finally:
                        for connection in held:
                            with contextlib.suppress(OSError):
                                connection.close()
                    # The service recovers once the held connections are gone.
                    deadline = time.monotonic() + 5
                    while True:
                        connection = http.client.HTTPSConnection(
                            '127.0.0.1', gateway.port, context=context, timeout=5)
                        try:
                            connection.request('GET', '/echo/abc')
                            response = connection.getresponse()
                            outcome = (response.status, response.read())
                        except (OSError, http.client.HTTPException):
                            outcome = None
                        finally:
                            connection.close()
                        if outcome == (200, b'{"amount":3}') or time.monotonic() > deadline:
                            break
                        time.sleep(.05)
                    self.assertEqual(outcome, (200, b'{"amount":3}'))

    def test_a_duplicated_forwarded_header_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            cert, key = _certificate(directory)
            with _environment(GOPYT_HTTP_GATEWAY_ADDR='127.0.0.1'):
                with running_server(MAX_HANDLERS=2) as (vm, port):
                    gateway = _Gateway(port, cert, key, pass_through=[
                        b'X-GoPyT-Client: alice', b'X-GoPyT-Client: root'])
                    gateway.start()
                    self.addCleanup(gateway.stop)
                    context = ssl.create_default_context(cafile=str(cert))
                    connection = http.client.HTTPSConnection(
                        '127.0.0.1', gateway.port, context=context, timeout=5)
                    try:
                        connection.request('GET', '/echo/abc')
                        response = connection.getresponse()
                        self.assertEqual((response.status, response.read()), (400, b''))
                    finally:
                        connection.close()
