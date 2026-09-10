"""Supported OS isolation profiles for untrusted evaluation on this runtime.

The VM is not an operating-system sandbox: application code runs in the host
process and the deployment's container is that boundary. Two paths do evaluate
input that the operator has not reviewed — Guard candidate acceptance and editor
checking — and those run as child processes, so an OS boundary can be installed
around them here.

The Linux profile puts the child in its own user, mount, network, PID, IPC and
UTS namespaces, pivots onto a private tmpfs root holding only read-only binds of
the interpreter and the material the child must read, gives it one writable
scratch mount, sets no-new-privileges, and applies explicit address-space, CPU,
descriptor and process limits. The child is its own session leader, so the whole
process tree is signalled and reaped together.

What this is not: it is not a hypervisor, it does not filter system calls, and
it does not defend a compromised host or kernel. It constrains what one child
process can reach. On a platform with no supported profile, a caller that
requires isolation is refused rather than silently running without it.
"""
import ctypes
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

CLONE_NEWNS = 0x00020000
CLONE_NEWUTS = 0x04000000
CLONE_NEWIPC = 0x08000000
CLONE_NEWUSER = 0x10000000
CLONE_NEWPID = 0x20000000
CLONE_NEWNET = 0x40000000
MS_RDONLY, MS_NOSUID, MS_NODEV, MS_NOEXEC = 1, 2, 4, 8
MS_REMOUNT, MS_BIND, MS_REC = 32, 4096, 16384
MS_PRIVATE = 1 << 18
MNT_DETACH = 2
PR_SET_NO_NEW_PRIVS = 38
SYS_PIVOT_ROOT = 155
LAUNCH_FAILED = 3

PROFILES = ('guard_candidate', 'editor_check')
SCRATCH = '/work'


class IsolationUnavailable(Exception):
    """No supported isolation profile can be installed on this platform."""


class Limits:
    """Explicit ceilings applied inside the isolated child."""

    __slots__ = ('address_space_bytes', 'cpu_seconds', 'descriptors', 'processes',
                 'scratch_bytes', 'root_bytes')

    def __init__(self, address_space_bytes=512 * 1024 ** 2, cpu_seconds=60,
                 descriptors=256, processes=64, scratch_bytes=32 * 1024 ** 2,
                 root_bytes=64 * 1024 ** 2):
        self.address_space_bytes = address_space_bytes
        self.cpu_seconds = cpu_seconds
        self.descriptors = descriptors
        self.processes = processes
        self.scratch_bytes = scratch_bytes
        self.root_bytes = root_bytes


def _libc():
    return ctypes.CDLL(None, use_errno=True)


def _checked(library, name, *args):
    function = getattr(library, name)
    ctypes.set_errno(0)
    if function(*args) != 0:
        code = ctypes.get_errno()
        raise OSError(code, f'{name}: {os.strerror(code)}')


def _mount(library, source, target, kind, flags, data=None):
    _checked(library, 'mount', (source or '').encode(), str(target).encode(),
             kind.encode() if kind else None, ctypes.c_ulong(flags),
             data.encode() if data else None)


def supported() -> bool:
    """Whether this platform has a supported isolation profile at all."""
    return sys.platform.startswith('linux')


_AVAILABILITY = None

# Probing only the first syscall is not enough: `unshare` can succeed on a host
# where the rest of the setup then fails, which would promise an isolation the
# caller never gets. The probe performs the whole setup and reports what broke.
_PROBE = (
    'import sys\n'
    'sys.path.insert(0, sys.argv[1])\n'
    'from gopyt.isolation import Limits, enter\n'
    'try:\n'
    '    enter([], Limits())\n'
    'except BaseException as error:\n'
    '    sys.stdout.write(f"{type(error).__name__}: {error}")\n'
    'else:\n'
    '    sys.stdout.write("ok")\n')


def available(refresh: bool = False) -> tuple[bool, str]:
    """Whether a profile can actually be installed here, and why not if it cannot.

    Support is a platform property; availability is a runtime one. A kernel with
    unprivileged user namespaces disabled has no way to install this profile,
    and saying so is the point of this probe. The probe runs as a fresh
    interpreter rather than a bare fork, because this may be called from a
    process that already has threads.
    """
    global _AVAILABILITY
    if not supported():
        return False, f'no supported isolation profile for {sys.platform}'
    if _AVAILABILITY is not None and not refresh:
        return _AVAILABILITY
    try:
        probe = subprocess.run(
            [sys.executable, '-I', '-c', _PROBE,
             str(Path(__file__).resolve().parent.parent)],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        _AVAILABILITY = (False, f'namespace probe failed: {error}')
        return _AVAILABILITY
    answer = probe.stdout.strip()
    if answer == 'ok':
        _AVAILABILITY = (True, 'linux namespaces')
    else:
        _AVAILABILITY = (False, 'unprivileged namespaces unavailable: '
                                + (answer or probe.stderr.strip() or 'unknown'))
    return _AVAILABILITY


USR_MERGED = ('/lib', '/lib64', '/bin', '/sbin')


def _merged_links():
    """Host top-level symlinks a usr-merged system needs, as (name, target).

    On such a system `/lib64` is a link into `/usr`, and the dynamic loader
    still opens it by that path. Binding `/usr` alone would leave the child
    unable to start any program.
    """
    links = []
    for candidate in USR_MERGED:
        if os.path.islink(candidate):
            links.append((candidate, os.readlink(candidate)))
    return links


def _readable_roots(extra):
    """Directories the child must read, kept as small as the interpreter allows."""
    roots = []
    for candidate in ('/usr',) + USR_MERGED:
        if os.path.isdir(candidate) and not os.path.islink(candidate):
            roots.append(candidate)
    prefix = os.path.realpath(sys.base_prefix)
    if not any(prefix == root or prefix.startswith(root + '/') for root in roots):
        roots.append(prefix)
    for path in extra:
        real = os.path.realpath(path)
        if not any(real == root or real.startswith(root + '/') for root in roots):
            roots.append(real)
    return roots


def enter(read_only, limits, writable=()):
    """Install the profile in the current process, then return.

    This runs in the launcher child before it executes the real work, so a
    failure here is a failed launch rather than an unisolated run.
    """
    import resource
    if not supported():
        raise IsolationUnavailable(f'no supported isolation profile for {sys.platform}')
    library = _libc()
    uid, gid = os.getuid(), os.getgid()
    _checked(library, 'unshare',
             ctypes.c_int(CLONE_NEWUSER | CLONE_NEWNS | CLONE_NEWNET | CLONE_NEWPID
                          | CLONE_NEWIPC | CLONE_NEWUTS))
    setgroups = Path('/proc/self/setgroups')
    try:
        if setgroups.read_text().strip() != 'deny':
            setgroups.write_text('deny')
    except OSError:
        # An outer user namespace may have denied setgroups already, leaving the
        # file read-only. That is the state this write wanted; anything else is
        # a real failure.
        if setgroups.read_text().strip() != 'deny':
            raise
    Path('/proc/self/uid_map').write_text(f'0 {uid} 1')
    Path('/proc/self/gid_map').write_text(f'0 {gid} 1')
    _checked(library, 'prctl', PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
    _mount(library, None, '/', None, MS_REC | MS_PRIVATE)
    new_root = tempfile.mkdtemp(prefix='gopyt-isolated-')
    _mount(library, 'tmpfs', new_root, 'tmpfs', MS_NOSUID | MS_NODEV,
           f'size={limits.root_bytes}')
    for path in read_only:
        target = os.path.join(new_root, path.lstrip('/'))
        os.makedirs(target, exist_ok=True)
        _mount(library, path, target, None, MS_BIND | MS_REC)
        _mount(library, path, target, None,
               MS_REMOUNT | MS_BIND | MS_REC | MS_RDONLY | MS_NOSUID | MS_NODEV)
    for path in writable:
        # A caller's own work directory stays writable; nothing else does.
        target = os.path.join(new_root, path.lstrip('/'))
        os.makedirs(target, exist_ok=True)
        _mount(library, path, target, None, MS_BIND | MS_REC)
        _mount(library, path, target, None,
               MS_REMOUNT | MS_BIND | MS_REC | MS_NOSUID | MS_NODEV)
    for name, target in _merged_links():
        link = os.path.join(new_root, name.lstrip('/'))
        if not os.path.lexists(link):
            os.symlink(target, link)
    scratch = os.path.join(new_root, SCRATCH.lstrip('/'))
    os.makedirs(scratch, exist_ok=True)
    _mount(library, 'tmpfs', scratch, 'tmpfs', MS_NOSUID | MS_NODEV | MS_NOEXEC,
           f'size={limits.scratch_bytes}')
    os.makedirs(os.path.join(new_root, 'proc'), exist_ok=True)
    previous = os.path.join(new_root, 'old')
    os.makedirs(previous, exist_ok=True)
    _checked(library, 'syscall', ctypes.c_long(SYS_PIVOT_ROOT),
             new_root.encode(), previous.encode())
    os.chdir('/')
    _checked(library, 'umount2', b'/old', ctypes.c_int(MNT_DETACH))
    os.rmdir('/old')
    # The private root is read-only too: only the scratch mount and any bind the
    # caller named as writable accept writes.
    _mount(library, None, '/', None, MS_REMOUNT | MS_RDONLY)
    resource.setrlimit(resource.RLIMIT_AS,
                       (limits.address_space_bytes, limits.address_space_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
    resource.setrlimit(resource.RLIMIT_NOFILE, (limits.descriptors, limits.descriptors))
    resource.setrlimit(resource.RLIMIT_NPROC, (limits.processes, limits.processes))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.chdir(SCRATCH)


def run(command, *, read_only=(), writable=(), limits=None, timeout=60,
        required=True, cwd=None, env=None):
    """Run `command` inside the profile, or refuse when it cannot be installed.

    `required=False` is the documented development escape: the command runs
    without a profile and the result says so, so nothing silently claims an
    isolation it did not get.
    """
    limits = limits or Limits()
    usable, reason = available()
    if not usable:
        if required:
            raise IsolationUnavailable(reason)
        return _launch(command, None, None, timeout, cwd, env), reason
    toolchain = str(Path(__file__).resolve().parent.parent)
    # A launcher path may be a symlink outside every bound directory; the child
    # can only execute what it can actually see, so resolve it first.
    resolved = [os.path.realpath(command[0]), *[str(part) for part in command[1:]]]
    writable = [os.path.realpath(path) for path in writable]
    roots = [root for root in _readable_roots(
        list(read_only) + [toolchain, os.path.dirname(resolved[0])])
        if not any(root == path or root.startswith(path + '/') for path in writable)]
    return (_launch(resolved, roots, limits, timeout, cwd, env, toolchain, writable),
            'linux namespaces')


def _launch(command, roots, limits, timeout, cwd, env, toolchain=None, writable=()):
    if roots is None:
        return subprocess.run(list(command), capture_output=True, text=True,
                              timeout=timeout, cwd=cwd, env=env)
    payload = repr({'read_only': list(roots), 'writable': list(writable),
                    'limits': {name: getattr(limits, name) for name in Limits.__slots__},
                    'command': list(command)})
    # Isolated import search, like every other trusted child: the toolchain path
    # is given explicitly so no candidate cwd or site hook can replace gopyt.
    script = ('import sys;sys.path.insert(0, sys.argv[1]);'
              'from gopyt.isolated_exec import main;'
              'raise SystemExit(main(sys.argv[2:]))')
    launcher = [sys.executable, '-I', '-c', script, toolchain, payload]
    process = subprocess.Popen(launcher, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, cwd=cwd, env=env, start_new_session=True)
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate(process)
        raise
    if process.returncode == LAUNCH_FAILED and err.startswith('isolation failed:'):
        # The profile could not be installed after all; the caller decides what
        # that means rather than receiving an unisolated result as if it were one.
        raise IsolationUnavailable(err.strip())
    return subprocess.CompletedProcess(launcher, process.returncode, out, err)


def _terminate(process):
    """Signal and reap the whole isolated tree, not just its leader."""
    for send in (signal.SIGKILL,):
        try:
            os.killpg(process.pid, send)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - kernel would have reaped it
        process.kill()
        process.communicate()


def describe() -> dict:
    """What this runtime can install, for a receipt or an operator report."""
    usable, reason = available()
    return {'schema': 'gopyt.isolation.v1', 'platform': sys.platform,
            'profiles': list(PROFILES), 'supported': supported(),
            'available': usable, 'detail': reason,
            'boundary': ('user, mount, network, PID, IPC and UTS namespaces with a '
                         'private tmpfs root, read-only system binds, one writable '
                         'scratch mount, no-new-privileges and explicit rlimits'),
            'not_provided': ['system-call filtering', 'hypervisor separation',
                             'defence of a compromised host or kernel',
                             'isolation of application code, which runs in the host process']}
