import fcntl
import hashlib
import json
import mmap
import os
import platform
import sys


def descriptors():
    result = {}
    for name in os.listdir('/proc/self/fd'):
        try:
            result[int(name)] = os.readlink('/proc/self/fd/' + name)
        except FileNotFoundError:
            pass
    return result


# Linux UAPI: linux/fcntl.h and asm-generic/fcntl.h.
assert sys.platform == 'linux'
baseline = descriptors()
fd = None
mapping = None
result = {'python': sys.version, 'platform': platform.platform(), 'stages': {}}
try:
    fd = os.memfd_create('gopyt-mapping-probe', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    payload = bytes(range(256)) * 16
    assert os.write(fd, payload) == len(payload)
    seals = 0x0008 | 0x0004 | 0x0002 | 0x0001
    fcntl.fcntl(fd, 1033, seals)
    assert fcntl.fcntl(fd, 1034) & seals == seals
    result['stages']['sealed_fd'] = len(descriptors()) - len(baseline)
    mapping = mmap.mmap(fd, len(payload), access=mmap.ACCESS_READ)
    result['stages']['mapped_with_original'] = len(descriptors()) - len(baseline)
    try:
        os.pwrite(fd, b'x', 0)
    except PermissionError:
        result['sealed_write_rejected'] = True
    else:
        raise AssertionError('sealed backing writable')
    os.close(fd)
    fd = None
    result['stages']['mapped_original_closed'] = len(descriptors()) - len(baseline)
    assert mapping[:] == payload
    result['payload_sha256'] = hashlib.sha256(mapping).hexdigest()
    mapping.close()
    mapping = None
    result['stages']['closed'] = len(descriptors()) - len(baseline)
    assert result['stages'] == {'sealed_fd': 1, 'mapped_with_original': 2, 'mapped_original_closed': 1, 'closed': 0}
    assert descriptors() == baseline
    result['pass'] = True
finally:
    if mapping is not None:
        mapping.close()
    if fd is not None:
        os.close(fd)
print(json.dumps(result, indent=2))
