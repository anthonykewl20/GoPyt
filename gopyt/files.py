"""Descriptor-relative regular-file access beneath a package directory."""

import os
import secrets
import stat
from contextlib import contextmanager


def segments(path):
    if not path or "\\" in path or "\0" in path:
        raise OSError("path")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise OSError("path")
    return parts


@contextmanager
def parent_directory(root, path, create=False):
    parts = segments(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(os.path.sep, flags)
    try:
        for component in os.path.abspath(root).split(os.path.sep)[1:]:
            if not component:
                continue
            child = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, parts[-1]
    finally:
        os.close(fd)


@contextmanager
def regular_file(root, path, write=False, *, check_context=lambda: None, buffering=-1):
    check_context()
    with parent_directory(root, path) as (parent, name):
        check_context()
        flags = os.O_WRONLY | os.O_CREAT if write else os.O_RDONLY
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o666, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("single-link regular file required")
            check_context()
            if write:
                os.ftruncate(fd, 0)
            check_context()
            with os.fdopen(fd, "wb" if write else "rb", buffering=buffering, closefd=False) as stream:
                yield stream
        finally:
            os.close(fd)


def atomic_write(root, path, data, create_parents=False):
    with parent_directory(root, path, create_parents) as (parent, name):
        mode = 0o666
        try:
            previous = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(previous.st_mode):
                raise OSError("regular file required")
            mode = stat.S_IMODE(previous.st_mode)
        except FileNotFoundError:
            pass
        temporary = ".gopyt-" + secrets.token_hex(12)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode, dir_fd=parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
