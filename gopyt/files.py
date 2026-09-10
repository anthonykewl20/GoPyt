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
def _owned_descriptor(owner):
    try:
        yield owner.fileno()
    except BaseException:
        # Keep the original operation/cancellation error. Failed cleanup stays
        # in the VM registry and is separately reported by teardown.
        owner.close()
        raise
    else:
        if not owner.close():
            raise OSError('descriptor cleanup incomplete')


@contextmanager
def _budgeted_parent(root, parts, create, descriptors):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    owner = descriptors.open(os.path.sep, flags)
    try:
        components = [(part, False) for part in os.path.abspath(root).split(os.path.sep)[1:] if part]
        components.extend((part, create) for part in parts[:-1])
        for component, mkdir in components:
            if mkdir:
                try:
                    os.mkdir(component, dir_fd=owner.fileno())
                except FileExistsError:
                    pass
            child = descriptors.open(component, flags, dir_fd=owner.fileno())
            previous, owner = owner, child
            # Transfer ownership first: if previous.close fails, unwind still
            # closes the child and the registry retains the uncertain parent.
            if not previous.close():
                raise OSError('directory cleanup incomplete')
        yield owner.fileno(), parts[-1]
    except BaseException:
        owner.close()
        raise
    else:
        if not owner.close():
            raise OSError('directory cleanup incomplete')


@contextmanager
def parent_directory(root, path, create=False, *, descriptors=None):
    parts = segments(path)
    if descriptors is not None:
        with _budgeted_parent(root, parts, create, descriptors) as result:
            yield result
        return
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
def regular_file(root, path, write=False, *, check_context=lambda: None, buffering=-1,
                 descriptors=None):
    check_context()
    with parent_directory(root, path, descriptors=descriptors) as (parent, name):
        check_context()
        flags = os.O_WRONLY | os.O_CREAT if write else os.O_RDONLY
        flags |= os.O_NOFOLLOW | os.O_NONBLOCK
        if descriptors is not None:
            owner = descriptors.open(name, flags, 0o666, dir_fd=parent)
            with _owned_descriptor(owner) as fd:
                with _regular_stream(fd, write, check_context, buffering) as stream:
                    yield stream
            return
        fd = os.open(name, flags, 0o666, dir_fd=parent)
        try:
            with _regular_stream(fd, write, check_context, buffering) as stream:
                yield stream
        finally:
            os.close(fd)


@contextmanager
def _regular_stream(fd, write, check_context, buffering):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise OSError("single-link regular file required")
    check_context()
    if write:
        os.ftruncate(fd, 0)
    check_context()
    with os.fdopen(fd, "wb" if write else "rb", buffering=buffering, closefd=False) as stream:
        yield stream


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
