"""Which resolved addresses an allowlisted outbound origin may actually reach.

An egress allowlist names origins, not addresses. A permitted name can still
resolve to a loopback, private or link-local address, either because the
deployment's own DNS says so or because an attacker controls the answer, so the
allowlist alone does not keep a request inside its intended network. This module
decides, for one origin host, which resolved addresses that host is allowed to
reach, and the connection path applies it to every candidate before connecting.

It is an egress boundary, not a network isolation guarantee: it constrains
addresses this runtime dials, and it does not stop anything else on the host.
"""
import ipaddress

# An operator naming loopback explicitly has chosen loopback. Every other host
# must reach a globally routable address, so a name cannot be pointed inward.
LOOPBACK_HOSTS = frozenset({'localhost', '127.0.0.1', '::1'})


class AddressPolicyError(OSError):
    """A resolved address is outside what its origin host may reach."""


def _unmap(address):
    """IPv4-mapped IPv6 hides an IPv4 address; classify the address it names."""
    mapped = getattr(address, 'ipv4_mapped', None)
    return mapped if mapped is not None else address


def literal_host(host: str):
    """The address a host names literally, or None when it is a name."""
    text = host.strip()
    if text.startswith('[') and text.endswith(']'):
        text = text[1:-1]
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def check_address(host: str, address: str) -> None:
    """Raise unless `host` is allowed to reach the resolved `address`."""
    try:
        resolved = _unmap(ipaddress.ip_address(address))
    except ValueError:
        raise AddressPolicyError(f'unresolvable connect address: {address!r}') from None
    literal = literal_host(host)
    if literal is not None:
        # A literal origin is pinned to itself: no name resolution can move it.
        if _unmap(literal) != resolved:
            raise AddressPolicyError(
                f'literal origin {host!r} must connect to itself, not {address!r}')
        return
    if host.lower() in LOOPBACK_HOSTS:
        if not resolved.is_loopback:
            raise AddressPolicyError(
                f'loopback origin {host!r} resolved to non-loopback {address!r}')
        return
    if not resolved.is_global:
        raise AddressPolicyError(
            f'origin {host!r} resolved to non-global address {address!r}')


def permitted(host: str, addresses) -> list:
    """Every candidate that `host` may reach, in the order given."""
    allowed = []
    for address in addresses:
        try:
            check_address(host, address)
        except AddressPolicyError:
            continue
        allowed.append(address)
    return allowed
