"""Host-issued authority for VM native resources; never a GoPyT value.

Trusted Python embedders issue roots and delegate attenuated handles. GoPyT code
cannot construct, serialize, inspect, replace, or revoke these host handles.
This is a VM boundary, not isolation from hostile Python in the same process.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Iterable
import threading
import time


class AuthorityError(ValueError):
    """Invalid grant, attempted amplification, or revoked delegation."""


_RIGHTS = frozenset(('database_read', 'database_write', 'file_read', 'file_write',
                    'network', 'listen', 'secrets'))
_PREFIX = frozenset(('database_read', 'database_write', 'file_read', 'file_write'))
_KEY = object()
MAX_DEPTH = 64
MAX_GRANTS = 256


def _grants(rights: Mapping[str, Iterable[str]]) -> Mapping[str, tuple[str, ...]]:
    if set(rights) - _RIGHTS:
        raise AuthorityError('unknown resource right')
    result = {}
    for right in sorted(_RIGHTS):
        entries = rights.get(right, ())
        if not isinstance(entries, (list, tuple)) or len(entries) > MAX_GRANTS:
            raise AuthorityError('grants must be a bounded list or tuple')
        canonical = []
        for entry in entries:
            if (not isinstance(entry, str) or not entry or len(entry) > 1024
                    or any(ord(ch) < 32 or ord(ch) == 127 for ch in entry)):
                raise AuthorityError('invalid resource grant')
            try:
                if len(entry.encode('utf-8')) > 1024:
                    raise AuthorityError('resource grant exceeds byte limit')
            except UnicodeError as exc:
                raise AuthorityError('resource grant requires UTF-8') from exc
            if entry == '*':
                canonical.append(entry)
                continue
            if '*' in entry:
                raise AuthorityError('only a whole wildcard is supported')
            if right.startswith('database_') and not entry.endswith('/'):
                raise AuthorityError('database grants require namespace prefixes')
            if right.startswith('file_'):
                from gopyt.files import segments
                try:
                    segments(entry[:-1] if entry.endswith('/') else entry)
                except OSError as exc:
                    raise AuthorityError('invalid file grant') from exc
            if right == 'network':
                from gopyt.check import normalize_origin
                normalized = normalize_origin(entry)
                if normalized is None or normalized != entry:
                    raise AuthorityError('network grants require canonical origins')
            if right == 'listen':
                # Hostnames are exact, without DNS or IP-range authority inference.
                host, sep, port = entry.rpartition(':')
                if (not host or not sep or not port.isascii() or not port.isdigit()
                        or len(port) > 5 or not 1 <= int(port) <= 65535
                        or str(int(port)) != port or any(c in host for c in '/\\@?#')):
                    raise AuthorityError('listen grants require host:port')
            if right == 'secrets':
                from gopyt.manifest import _is_snake
                if not _is_snake(entry):
                    raise AuthorityError('invalid secret grant')
            canonical.append(entry)
        result[right] = tuple(sorted(set(canonical)))
    return MappingProxyType(result)


def _covers(right: str, grant: str, resource: str) -> bool:
    return (grant == '*' or grant == resource
            or right in _PREFIX and grant.endswith('/') and resource.startswith(grant))


@dataclass(frozen=True)
class _Node:
    grants: Mapping[str, tuple[str, ...]]
    parent: _Node | None
    depth: int
    lock: object
    revoked: object
    expires_ns: int | None = None


class ResourceAuthority:
    """Opaque host handle. Use issue(), delegate(), and revoke().

    Omitted rights deny access. Delegation cannot widen rights. Revoking any
    ancestor prevents future admission throughout its descendant tree. Already
    admitted native operations may finish, including durable writes.
    """
    __slots__ = ('__node',)

    def __init__(self, key, node):
        if key is not _KEY:
            raise TypeError('use ResourceAuthority.issue')
        self.__node = node

    @classmethod
    def issue(cls, **rights) -> ResourceAuthority:
        return cls(_KEY, _Node(_grants(rights), None, 0,
                              threading.Lock(), threading.Event()))

    def delegate(self, *, ttl_ms: int | None = None, **rights) -> ResourceAuthority:
        if ttl_ms is not None and (type(ttl_ms) is not int or not 1 <= ttl_ms <= 86_400_000):
            raise AuthorityError('authority lifetime must be 1..86400000 milliseconds')
        grants = _grants(rights)
        node = self.__node
        with node.lock:
            if not self._live() or node.depth >= MAX_DEPTH:
                raise AuthorityError('revoked authority or delegation depth limit')
            for right, entries in grants.items():
                if any(not any(_covers(right, parent, child)
                               for parent in node.grants[right]) for child in entries):
                    raise AuthorityError('delegation cannot widen authority')
            return ResourceAuthority(_KEY, _Node(grants, node, node.depth + 1,
                                                node.lock, threading.Event(),
                                                None if ttl_ms is None else time.monotonic_ns() + ttl_ms * 1_000_000))

    def revoke(self) -> None:
        with self.__node.lock:
            self.__node.revoked.set()

    def _live(self) -> bool:
        node = self.__node
        while node is not None:
            if (node.revoked.is_set() or node.expires_ns is not None
                    and time.monotonic_ns() >= node.expires_ns):
                return False
            node = node.parent
        return True

    def admits(self, requests: Iterable[tuple[str, str]]) -> bool:
        """Atomic admission relative to revocation, including every batch key."""
        with self.__node.lock:
            return self._live() and all(
                right in _RIGHTS and any(_covers(right, grant, resource)
                                        for grant in self.__node.grants[right])
                for right, resource in requests)

    def is_descendant_of(self, other: ResourceAuthority) -> bool:
        node = self.__node
        while node is not None:
            if node is other.__node:
                return True
            node = node.parent
        return False
