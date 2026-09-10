# OS isolation profiles for untrusted evaluation

This amendment defines the isolation profiles this runtime installs, what they
constrain, and what they do not. It changes no GoPyT source syntax and no closed
standard-library signature.

## What is and is not isolated

Application code runs in the host process. The VM is not an operating-system
sandbox for it, and nothing here changes that: the deployment's container and
service account remain the boundary for application execution, exactly as the
frozen threat model records.

Two paths do evaluate input an operator has not reviewed, and both run as child
processes, so an OS boundary can be installed around them:

- **`guard_candidate`** — Guard acceptance of a candidate edit.
- **`editor_check`** — checking an editor's unsaved project snapshot.

Compilation of operator-controlled sources and ordinary application execution
are outside these profiles by decision, and the deployment owns them.

## The Linux profile

The child is placed in its own user, mount, network, PID, IPC and UTS
namespaces. It then pivots onto a private `tmpfs` root that contains only:

- read-only, `nosuid`, `nodev` bind mounts of the interpreter installation, the
  GoPyT toolchain and any directory the caller named as readable;
- the top-level symlinks a usr-merged host needs, so the dynamic loader can
  still open `/lib64`;
- one `tmpfs` scratch mount at `/work`, `nosuid`, `nodev`, `noexec`, which is
  the child's working directory;
- any directory the caller explicitly named as writable.

The private root itself is then remounted read-only, so only the scratch mount
and the named writable binds accept writes. Nothing else on the host filesystem
is present in the child's mount namespace at all — not merely unreadable.

`PR_SET_NO_NEW_PRIVS` is set, so no execution in the tree can gain privileges
through a set-user-id binary. Explicit `RLIMIT_AS`, `RLIMIT_CPU`,
`RLIMIT_NOFILE`, `RLIMIT_NPROC` and `RLIMIT_CORE` ceilings apply, with the
`tmpfs` sizes bounding what the child can write. The network namespace has only
loopback, so no address outside the child is reachable.

The launcher is its own session leader. On timeout the whole process group is
signalled and reaped, and the PID namespace dies with its first process, so no
part of the tree survives to be inherited by the host's init.

## Availability and failing closed

Support is a platform property; availability is a runtime one. A kernel with
unprivileged user namespaces disabled has no way to install this profile. The
runtime probes for that in a fresh interpreter rather than a bare fork, because
the caller may already have threads, and caches the answer.

Probing only the first system call is not enough: `unshare` can succeed on a
host where the rest of the setup then fails, which would promise an isolation
the caller never receives. The probe therefore performs the whole entry path.
An outer user namespace that has already denied `setgroups` leaves that file
read-only, which is the state the setup wanted, so it is accepted rather than
treated as a failure.

If installing the profile still fails after a positive probe, that is reported
as unavailability, never as an unisolated result presented as an isolated one.

`GOPYT_GUARD_ISOLATION` selects the policy for Guard acceptance:

- `required` — refuse to evaluate a candidate when no profile can be installed.
  A deployment that relies on isolation should use this.
- `preferred` (default) — install one where possible; the receipt records
  exactly what was installed and, when nothing was, why.
- `off` — never install one, and say so in the receipt.

Every receipt carries an `isolation` record with the platform, the policy,
whether a profile was actually installed, the boundary it provides and the list
of things it does not. A receipt therefore never implies an isolation it did not
have.

macOS is a development-only platform in the frozen envelope and has no supported
profile here. On it, and on any kernel where namespaces are unavailable, editor
checking keeps its existing bounded subprocess and time limits rather than
losing checking entirely, and Guard follows the configured policy.

## Explicitly not provided

- No system-call filtering. A seccomp policy is not installed.
- No hypervisor separation.
- No defence of a compromised host or kernel, and no defence against a kernel
  vulnerability reachable from an unprivileged user namespace.
- No isolation of application code, which runs in the host process.
- No claim that these finite hostile fixtures enumerate what a determined
  attacker would attempt.
