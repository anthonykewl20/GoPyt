"""Linux research prototype: immutable sealed i64 columns, not a GoPyT native.

Seal-before-sharing pattern adapted from Ahmed S. Darwish's Unlicense example:
a-darwish/memfd-examples/server.c@172d805b24e0c3283934ba009bfdadcc3ab92bf7.
No donor runtime dependency. Data is plaintext in memory.
"""
from contextlib import contextmanager
import fcntl
import mmap
import os
import struct
import sys

# Linux UAPI constants; some Python builds omit their fcntl exports.
ADD_SEALS = getattr(fcntl, "F_ADD_SEALS", 1033)
GET_SEALS = getattr(fcntl, "F_GET_SEALS", 1034)
SEALS = 0x0001 | 0x0002 | 0x0004 | 0x0008

HEADER = struct.Struct('<8sQ')
VALUE = struct.Struct('<q')
MAGIC = b'GPYC001\0'
MAX_BYTES = 64 * 1024 * 1024


def available():
    return sys.platform == 'linux' and hasattr(os, 'memfd_create')


def validate(buffer):
    if len(buffer) < HEADER.size or len(buffer) > MAX_BYTES:
        raise ValueError('column byte limit')
    magic, count = HEADER.unpack_from(buffer)
    if magic != MAGIC or HEADER.size + count * VALUE.size != len(buffer):
        raise ValueError('invalid column header or exact length')
    return count


def value_at(buffer, index):
    count = validate(buffer)
    if type(index) is not int or not 0 <= index < count:
        raise IndexError('column index')
    return VALUE.unpack_from(buffer, HEADER.size + index * VALUE.size)[0]


@contextmanager
def sealed_column(data):
    validate(data)
    if not available():
        raise OSError('Linux memfd sealing required')
    fd = os.memfd_create('gopyt-column', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        remaining = memoryview(data)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0: raise OSError('short column write')
            remaining = remaining[written:]
        # Unlike the reference's writable mapping, populate through write first.
        # Seal growth too, then prohibit any subsequent changes to seal policy.
        flags = SEALS
        fcntl.fcntl(fd, ADD_SEALS, flags)
        if fcntl.fcntl(fd, GET_SEALS) & flags != flags:
            raise OSError('incomplete seal set')
        with mmap.mmap(fd, len(data), access=mmap.ACCESS_READ) as mapping:
            yield fd, mapping
    finally:
        os.close(fd)
