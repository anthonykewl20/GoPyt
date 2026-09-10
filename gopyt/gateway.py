"""The boundary between a trusted TLS gateway and the loopback service.

Strict mode terminates TLS at a gateway and listens on loopback, so the service
itself never sees the client's connection. Anything the gateway asserts about
that client arrives as an ordinary header, which a client could also send. This
module decides when such a header may be believed: only when the connection came
from the configured gateway address, and never otherwise.

Configuring a gateway also makes the service fail closed for every other peer,
because the deployment has stated that all traffic arrives through the gateway.
This constrains what this process believes and admits. It does not authenticate
the gateway itself, which remains the deployment's responsibility along with the
network path that keeps other peers away from the loopback listener.
"""
import ipaddress
import os

from gopyt.security_config import SecurityError

DEFAULT_FORWARDED_HEADER = 'X-GoPyT-Client'
MAX_FORWARDED_LENGTH = 256


class GatewayPolicy:
    """Configured trust in a gateway, or the absence of any such trust."""

    __slots__ = ('peer', 'header', 'required', 'rate_tokens', 'rate_refill_ms')

    def __init__(self, peer=None, header=DEFAULT_FORWARDED_HEADER, required=False,
                 rate_tokens=None, rate_refill_ms=None):
        self.peer = peer
        self.header = header
        self.required = required
        self.rate_tokens = rate_tokens
        self.rate_refill_ms = rate_refill_ms

    @property
    def trusts_a_gateway(self) -> bool:
        return self.peer is not None

    @property
    def rate_limited(self) -> bool:
        return self.rate_tokens is not None

    def accepts_peer(self, address) -> bool:
        """Whether a connection from `address` may be served at all."""
        if self.peer is None:
            return True
        try:
            return ipaddress.ip_address(address) == ipaddress.ip_address(self.peer)
        except (ValueError, TypeError):
            return False

    def forwarded_identity(self, peer, values):
        """The client identity the gateway asserted, or a refusal.

        Returns (identity, problem). `identity` is None whenever nothing may be
        believed. `problem` is non-None only when the request must be refused
        rather than merely treated as unidentified.
        """
        if not self.trusts_a_gateway or not self.accepts_peer(peer):
            # Nothing outside a trusted gateway may assert a client identity.
            return None, ('identity' if self.required else None)
        if len(values) > 1:
            return None, 'duplicate'
        if not values:
            return None, ('identity' if self.required else None)
        value = values[0].strip()
        if (not value or len(value) > MAX_FORWARDED_LENGTH or not value.isascii()
                or any(ch < 33 or ch > 126 for ch in value.encode('ascii'))):
            return None, 'malformed'
        return value, None


def _numerical(raw, name):
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        raise SecurityError(f'{name} must be a numerical address') from None


def _header_name(raw):
    if not raw or len(raw) > 64 or not raw.isascii():
        raise SecurityError('forwarded identity header must be 1..64 ASCII characters')
    for character in raw:
        if not (character.isalnum() or character == '-'):
            raise SecurityError('forwarded identity header must be a token')
    return raw


def _rate(raw):
    parts = raw.split('/')
    if len(parts) != 2 or not all(part.isdigit() and part.isascii() for part in parts):
        raise SecurityError('rate limit must be written tokens/refill_ms')
    tokens, refill = int(parts[0]), int(parts[1])
    if not 1 <= tokens <= 1_000_000 or not 1 <= refill <= 3_600_000:
        raise SecurityError('rate limit outside supported bounds')
    return tokens, refill


def from_environment(environ=None) -> GatewayPolicy:
    """Read the gateway boundary, refusing a configuration that cannot hold."""
    environ = os.environ if environ is None else environ
    peer = environ.get('GOPYT_HTTP_GATEWAY_ADDR')
    header = environ.get('GOPYT_HTTP_FORWARDED_HEADER')
    required = environ.get('GOPYT_HTTP_FORWARDED_REQUIRED')
    rate = environ.get('GOPYT_HTTP_RATE')
    if peer is None:
        # A forwarded header without a gateway to believe would be a client's
        # own claim, so refuse the configuration rather than ignore half of it.
        if header is not None or required is not None:
            raise SecurityError('forwarded identity requires GOPYT_HTTP_GATEWAY_ADDR')
        return GatewayPolicy(rate_tokens=None if rate is None else _rate(rate)[0],
                             rate_refill_ms=None if rate is None else _rate(rate)[1])
    policy = GatewayPolicy(peer=_numerical(peer, 'GOPYT_HTTP_GATEWAY_ADDR'),
                           header=_header_name(header) if header else DEFAULT_FORWARDED_HEADER)
    if required is not None:
        if required not in ('0', '1'):
            raise SecurityError('GOPYT_HTTP_FORWARDED_REQUIRED must be 0 or 1')
        policy.required = required == '1'
    if rate is not None:
        policy.rate_tokens, policy.rate_refill_ms = _rate(rate)
    return policy
