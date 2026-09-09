"""Trusted-host database authority, checked before storage and cache access.

This is service-level confinement, not HTTP identity or tenant authentication.
GoPyT programs cannot create or widen these grants.
"""
from dataclasses import dataclass
import json
import os

from gopyt.security_config import secret_file, SecurityError


@dataclass(frozen=True)
class DatabaseAuthority:
    read: tuple[str, ...]
    write: tuple[str, ...]

    def permits(self, keys, *, read=False, write=False):
        for required, grants in ((read, self.read), (write, self.write)):
            if required and any(not any(prefix == '*' or key.startswith(prefix)
                                        for prefix in grants) for key in keys):
                return False
        return True


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SecurityError('duplicate database policy field')
        result[key] = value
    return result


def database_authority(root):
    path = os.environ.get('GOPYT_DB_POLICY_FILE')
    if path is None:
        return None
    data = secret_file(path, root, 65536)
    try:
        policy = json.loads(data, object_pairs_hook=_unique)
    except (ValueError, UnicodeError) as exc:
        raise SecurityError('invalid database policy') from exc
    if (not isinstance(policy, dict) or set(policy) != {'version', 'read', 'write'}
            or type(policy['version']) is not int or policy['version'] != 1):
        raise SecurityError('invalid database policy schema')
    for name in ('read', 'write'):
        grants = policy[name]
        if not isinstance(grants, list) or len(grants) > 256:
            raise SecurityError('invalid database policy grants')
        for prefix in grants:
            if (not isinstance(prefix, str) or not prefix or len(prefix) > 256
                    or any(ord(ch) < 32 or ord(ch) == 127 for ch in prefix)
                    or (prefix != '*' and (not prefix.endswith('/') or '*' in prefix))):
                raise SecurityError('database grants require namespace prefixes or explicit wildcard')
    return DatabaseAuthority(tuple(policy['read']), tuple(policy['write']))
