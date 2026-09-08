"""Opt-in host security controls; secrets remain outside application packages."""
import hashlib
import os
from pathlib import Path
import secrets
import stat
from gopyt.files import parent_directory

MAGIC=b'GOPYT-SIV1\0'
OVERHEAD=len(MAGIC)+12+16


class SecurityError(Exception):
    pass


def strict():
    value=os.environ.get('GOPYT_SECURITY_PROFILE','development')
    if value not in ('development','strict'):
        raise SecurityError('unknown security profile')
    return value=='strict'


def secret_file(path,root,maximum):
    if not path or not os.path.isabs(path):raise SecurityError('absolute secret file path required')
    absolute=Path(path)
    if root is not None and absolute.is_relative_to(Path(os.path.abspath(root))):
        raise SecurityError('secret must be outside application package')
    try:
        with parent_directory('/',path.lstrip('/')) as (parent,name):
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_mode & 0o077:
                raise SecurityError('secret requires a private single-link regular file')
            if info.st_uid not in (os.geteuid(),0):raise SecurityError('unexpected secret owner')
            value=os.read(fd,maximum+1)
            if not value or len(value)>maximum:raise SecurityError('invalid secret length')
            return value
        finally:os.close(fd)
    except OSError as exc:raise SecurityError('secret file unavailable') from exc


def storage_cipher(root):
    path=os.environ.get('GOPYT_STORE_KEY_FILE')
    if not path:
        if strict():raise SecurityError('strict profile requires a storage key')
        return None
    strict()
    key=secret_file(path,root,32)
    if len(key)!=32:raise SecurityError('storage key must contain exactly 32 bytes')
    context=os.environ.get('GOPYT_STORE_ID','')
    if not context or len(context.encode())>256:raise SecurityError('stable storage context required')
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV
        from cryptography.exceptions import UnsupportedAlgorithm
    except ImportError as exc:raise SecurityError('supported security extra required') from exc
    try:
        cipher=AESGCMSIV(key)
    except (ValueError,UnsupportedAlgorithm) as exc:
        raise SecurityError('supported security extra required') from exc
    aad=MAGIC+context.encode()
    return cipher,aad,hashlib.sha256(key+aad).digest()


def seal(data,setting):
    if setting is None:return data
    cipher,aad,_=setting;nonce=secrets.token_bytes(12)
    return MAGIC+nonce+cipher.encrypt(nonce,data,aad)


def unseal(data,setting):
    if setting is None:
        if data.startswith(MAGIC):raise SecurityError('encrypted storage requires its key')
        return data
    if not data.startswith(MAGIC) or len(data)<OVERHEAD:
        raise SecurityError('authenticated storage format required')
    cipher,aad,_=setting;nonce=data[len(MAGIC):len(MAGIC)+12]
    try:return cipher.decrypt(nonce,data[len(MAGIC)+12:],aad)
    except Exception as exc:raise SecurityError('storage authentication failed') from exc


def http_token(root,address):
    required=strict()
    path=os.environ.get('GOPYT_HTTP_TOKEN_FILE')
    if required:
        import ipaddress
        try:local=ipaddress.ip_address(address[0]).is_loopback
        except ValueError:local=False
        if not local:raise SecurityError('strict HTTP requires loopback behind a TLS gateway')
        storage_cipher(root)
    if not path:
        if required:raise SecurityError('strict HTTP requires an authentication token')
        return None
    token=secret_file(path,root,256).strip()
    if not 32<=len(token)<=256 or any(ch<33 or ch>126 for ch in token):
        raise SecurityError('token must contain 32..256 printable non-space ASCII bytes')
    return b'Bearer '+token
