"""Linux sealed mapping backing for checked Buffer handles.

Linux UAPI constants are explicit because supported Python builds may omit their
fcntl exports. No descriptor or memory export is returned to application code.
"""
import fcntl
import mmap
import os
import sys


class MappingInitializationError(Exception):
    """An unpublished owner needs retained cleanup; preserve the original error."""
    def __init__(self, owner, cause):
        super().__init__('mapping initialization cleanup incomplete')
        self.owner = owner
        self.cause = cause


class MappingStorage:
    def __init__(self):
        self.mapping = None
        self._fd = None
        self._fd_error = None
        self._fd_reservation = None

    def initialize(self, data, budget, check):
        if sys.platform != 'linux' or not hasattr(os, 'memfd_create'):
            raise OSError('sealed mappings unavailable')
        # The main reservation already includes mmap's duplicated descriptor.
        # Acquire nothing until the original descriptor is also admitted.
        self._fd_reservation = budget.reserve(descriptors=1)
        check()
        self._fd = os.memfd_create('gopyt-buffer', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        with memoryview(data) as source:
            offset = 0
            while offset < len(data):
                check()
                with source[offset:offset + 65536] as part:
                    written = os.write(self._fd, part)
                if written <= 0:
                    raise OSError('mapping backing write failed')
                offset += written
        check()
        # F_ADD_SEALS/F_GET_SEALS; SEAL, SHRINK, GROW, WRITE (Linux UAPI).
        fcntl.fcntl(self._fd, 1033, 15)
        if fcntl.fcntl(self._fd, 1034) & 15 != 15:
            raise OSError('mapping backing seals incomplete')
        check()
        self.mapping = mmap.mmap(self._fd, len(data), access=mmap.ACCESS_READ)
        self._close_original()
        check()

    def _close_original(self):
        if self._fd_error is not None:
            # Never retry an ambiguous raw close: the number may be reused.
            # Keep the charge and report unresolved cleanup at VM teardown.
            raise OSError('mapping descriptor cleanup uncertain')
        if self._fd is not None:
            fd, self._fd = self._fd, None
            try:
                os.close(fd)
            except BaseException as error:
                self._fd_error = type(error).__name__
                raise
        if self._fd_reservation is not None:
            self._fd_reservation.release()
            self._fd_reservation = None

    def close(self):
        try:
            if self.mapping is not None:
                self.mapping.close()
                self.mapping = None
        finally:
            self._close_original()
