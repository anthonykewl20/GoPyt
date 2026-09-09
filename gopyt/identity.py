"""Trusted-host session broker binding opaque bearer credentials to VM grants.

This module is not a login/IdP implementation. Only the trusted host provisions
verified subjects and issues sessions after its authentication/MFA procedure.
No HTTP-supplied subject, tenant, role, or forwarding header is trusted here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import secrets
import threading

from gopyt.manifest import _is_snake
from gopyt.resource_authority import ResourceAuthority, AuthorityError, _grants

MAX_POLICIES = 1024
MAX_SESSIONS = 4096
MAX_SESSION_MS = 3_600_000
_METHODS = frozenset(('GET', 'POST', 'PUT', 'PATCH', 'DELETE'))


@dataclass(frozen=True)
class RequestIdentity:
    subject: str
    tenant: str
    session_id: str


@dataclass(frozen=True)
class AuthenticatedRequest:
    identity: RequestIdentity
    authority: ResourceAuthority
    routes: frozenset[tuple[str, str]]


@dataclass(frozen=True)
class _Policy:
    authority: ResourceAuthority
    rights: object
    routes: frozenset[tuple[str, str]]


def _key(subject: str, tenant: str) -> tuple[str, str]:
    if (not isinstance(subject, str) or not 1 <= len(subject) <= 128
            or not subject.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in subject)
            or not isinstance(tenant, str) or len(tenant) > 64 or not _is_snake(tenant)):
        raise AuthorityError('invalid subject or tenant identity')
    return subject, tenant


def _routes(routes) -> frozenset[tuple[str, str]]:
    if not isinstance(routes, (list, tuple)) or len(routes) > 256:
        raise AuthorityError('routes must be a bounded list or tuple')
    result = set()
    for item in routes:
        if (not isinstance(item, (list, tuple)) or len(item) != 2
                or not isinstance(item[0], str) or not isinstance(item[1], str)):
            raise AuthorityError('routes require method and declared route template')
        method, path = item
        if (method not in _METHODS or not path.startswith('/') or len(path) > 1024
                or not path.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in path)):
            raise AuthorityError('invalid route grant')
        result.add((method, path))
    return frozenset(result)


def _token_digest(authorization: str) -> bytes | None:
    if not isinstance(authorization, str) or len(authorization) != 50:
        return None
    scheme, separator, token = authorization.partition(' ')
    if (scheme.lower() != 'bearer' or not separator
            or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in token)):
        return None
    return hashlib.sha256(token.encode('ascii')).digest()


class SessionBroker:
    """In-process, bounded and deny-by-default verified-subject/session registry.

    Policies are replaced atomically; replacement revokes their old descendants.
    Bearer credentials are random 256-bit values returned once, stored by digest,
    never persisted/logged here. Restart loses all sessions and denies old tokens.
    """
    def __init__(self, authority: ResourceAuthority, *, module: str):
        if type(authority) is not ResourceAuthority:
            raise TypeError('session broker requires host resource authority')
        if (not isinstance(module, str) or not 1 <= len(module.split('.')) <= 8
                or not all(_is_snake(part) for part in module.split('.'))):
            raise AuthorityError('broker requires an exact GoPyT module audience')
        self.authority = authority
        self.module = module
        self._bound = False
        self._policies: dict[tuple[str, str], _Policy] = {}
        self._sessions: dict[bytes, AuthenticatedRequest] = {}
        self._lock = threading.Lock()

    def _bind(self) -> None:
        with self._lock:
            if self._bound:
                raise AuthorityError('session broker is already bound to a VM')
            self._bound = True

    def provision(self, subject: str, tenant: str, *, routes=(), **rights) -> None:
        key = _key(subject, tenant)
        allowed_routes = _routes(routes)
        frozen = _grants(rights)
        # Every DB/file grant is confined to the verified tenant selected by the
        # trusted host. Network and named secret grants still require attenuation.
        for name, grants in frozen.items():
            if name in ('database_read', 'database_write', 'file_read', 'file_write'):
                prefix = ('tenant/' if name.startswith('database_') else 'tenants/') + tenant + '/'
                if not isinstance(grants, (list, tuple)) or any(
                        not isinstance(grant, str) or not grant.startswith(prefix) for grant in grants):
                    raise AuthorityError('resource grant must stay in the verified tenant namespace')
            if name == 'listen' and grants:
                raise AuthorityError('request sessions cannot acquire listeners')
        # Delegation validates and freezes all caller-owned lists.
        authority = self.authority.delegate(**frozen)
        with self._lock:
            if key not in self._policies and len(self._policies) >= MAX_POLICIES:
                raise AuthorityError('identity policy capacity exceeded')
            previous = self._policies.get(key)
            if previous is not None:
                previous.authority.revoke()
            self._policies[key] = _Policy(authority, frozen, allowed_routes)
            self._purge()

    def revoke(self, subject: str, tenant: str) -> None:
        with self._lock:
            previous = self._policies.pop(_key(subject, tenant), None)
            if previous is not None:
                previous.authority.revoke()
            self._purge()

    def _purge(self) -> None:
        self._sessions = {digest: session for digest, session in self._sessions.items()
                          if session.authority.admits(())}

    def issue(self, subject: str, tenant: str, *, ttl_ms: int = 900_000) -> str:
        key = _key(subject, tenant)
        if type(ttl_ms) is not int or not 1 <= ttl_ms <= MAX_SESSION_MS:
            raise AuthorityError('session lifetime must be 1..3600000 milliseconds')
        with self._lock:
            self._purge()
            policy = self._policies.get(key)
            if policy is None or not policy.authority.admits(()):
                raise AuthorityError('subject is not provisioned')
            if len(self._sessions) >= MAX_SESSIONS:
                raise AuthorityError('session capacity exceeded')
            # Retry a collision without overwriting an existing session.
            for _ in range(4):
                token = secrets.token_urlsafe(32)
                digest = _token_digest('Bearer ' + token)
                if digest is not None and digest not in self._sessions:
                    break
            else:
                raise AuthorityError('session credential allocation failed')
            grant = policy.authority.delegate(ttl_ms=ttl_ms, **policy.rights)
            context = AuthenticatedRequest(RequestIdentity(subject, tenant, secrets.token_hex(16)),
                                           grant, policy.routes)
            self._sessions[digest] = context
            return token

    def authenticate(self, authorization: str) -> AuthenticatedRequest | None:
        digest = _token_digest(authorization)
        if digest is None:
            return None
        with self._lock:
            session = self._sessions.get(digest)
            if session is None:
                return None
            if not session.authority.admits(()):
                self._sessions.pop(digest, None)
                return None
            return session

    def revoke_session(self, token: str) -> None:
        digest = _token_digest('Bearer ' + token)
        with self._lock:
            session = self._sessions.pop(digest, None)
            if session is not None:
                session.authority.revoke()
