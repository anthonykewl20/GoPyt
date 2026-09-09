"""Opt-in host security controls; secrets remain outside application packages."""
import hashlib
import json
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
    ring_path=os.environ.get('GOPYT_STORE_KEYRING_FILE')
    if path and ring_path:raise SecurityError('select one storage key source')
    if not path and not ring_path:
        if strict():raise SecurityError('strict profile requires a storage key')
        return None
    strict()
    if ring_path:
        keys, active = _keyring(secret_file(ring_path,root,4096))
    else:
        key=secret_file(path,root,32)
        if len(key)!=32:raise SecurityError('storage key must contain exactly 32 bytes')
        keys, active = {'single': key}, 'single'
    context=os.environ.get('GOPYT_STORE_ID','')
    if not context or len(context.encode())>256:raise SecurityError('stable storage context required')
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV
        from cryptography.exceptions import UnsupportedAlgorithm
    except ImportError as exc:raise SecurityError('supported security extra required') from exc
    try:
        ciphers={name:AESGCMSIV(key) for name,key in keys.items()}
    except (ValueError,UnsupportedAlgorithm) as exc:
        raise SecurityError('supported security extra required') from exc
    aad=MAGIC+context.encode()
    cipher = _KeyringCipher(ciphers, active)
    identity = json.dumps({'active':active,'keys':{name:key.hex() for name,key in keys.items()}},sort_keys=True).encode()
    return cipher,aad,hashlib.sha256(identity+aad).digest()


def _keyring(raw):
    def unique(pairs):
        result={}
        for name,value in pairs:
            if name in result:raise SecurityError('duplicate keyring field')
            result[name]=value
        return result
    try:data=json.loads(raw,object_pairs_hook=unique)
    except (ValueError,UnicodeError,RecursionError) as exc:raise SecurityError('invalid storage keyring') from exc
    if (not isinstance(data,dict) or set(data)!={'version','active','keys'}
        or type(data['version']) is not int or data['version']!=1
        or not isinstance(data['keys'],dict) or not 1<=len(data['keys'])<=4
        or not isinstance(data['active'],str) or data['active'] not in data['keys']):
        raise SecurityError('invalid storage keyring schema')
    keys={}
    for name,value in data['keys'].items():
        if (not name or len(name)>32 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in name)
            or not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdefABCDEF' for c in value)):
            raise SecurityError('invalid storage keyring entry')
        keys[name]=bytes.fromhex(value)
    if len(set(keys.values()))!=len(keys):raise SecurityError('duplicate storage key material')
    return keys,data['active']


class _KeyringCipher:
    """Bounded authenticated fallback; the existing snapshot format is retained."""
    def __init__(self,ciphers,active):
        self.active=ciphers[active]
        self.readers=[self.active]+[cipher for name,cipher in ciphers.items() if name!=active]

    def encrypt(self,nonce,data,aad):
        return self.active.encrypt(nonce,data,aad)

    def decrypt(self,nonce,data,aad):
        from cryptography.exceptions import InvalidTag
        for cipher in self.readers:
            try:return cipher.decrypt(nonce,data,aad)
            except InvalidTag:pass
        raise InvalidTag


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


def http_token(root,address,*,session_auth=False):
    required=strict()
    path=os.environ.get('GOPYT_HTTP_TOKEN_FILE')
    if session_auth and path:
        raise SecurityError('select service token or verified sessions, not both')
    if required:
        import ipaddress
        try:local=ipaddress.ip_address(address[0]).is_loopback
        except ValueError:local=False
        if not local:raise SecurityError('strict HTTP requires loopback behind a TLS gateway')
        storage_cipher(root)
    if not path:
        if required and not session_auth:raise SecurityError('strict HTTP requires an authentication token')
        return None
    return http_token_file(path,root)


def http_token_file(path,root):
    """Read one private service-token snapshot; callers pin the configured path."""
    token=secret_file(path,root,256).strip()
    if not 32<=len(token)<=256 or any(ch<33 or ch>126 for ch in token):
        raise SecurityError('token must contain 32..256 printable non-space ASCII bytes')
    return b'Bearer '+token
