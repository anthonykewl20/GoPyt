OS isolation profile evidence

Frozen source/runtime identity is in source.json (head 3efdc40, runtime
sha256:04e827adb8915c13e58a0a1b3e4f43d48670933f8b85263bd3ff8f5588ae8749,
595 file hashes). profile.json records what this host could actually install
and, importantly, what the profile does not provide.

Python 3.14.7 full language suite: 1,178 tests passed in 614.898s (full314.log).
Python 3.11.16 full language suite: 1,178 tests passed in 684.444s (full311.log),
with one test skipped because that run used an exported copy with no Git metadata.

Two wheel builds under SOURCE_DATE_EPOCH=1788998400 are byte-identical
(wheel SHA256 e3626cb48dab142c88840c62bccce2b286c3b432be854965327395214871435e,
64 runtime files). Installed-runtime smoke, format 2 to format 3 upgrade and
rollback, 22 Guard tests, 22-module stdlib synchronization and all four
contract-demo outcomes passed.

The hostile fixtures in gopyt/test_isolation.py run against a real profile and
skip, with the kernel's reason recorded, where one cannot be installed. They
cover an unreachable network with a working loopback stack, a host filesystem
that is absent rather than merely unreadable, a read-only private root with only
the named writable bind accepting writes, bounded address space, process count
and descriptors, no-new-privileges, and host-side confirmation that a runaway
process tree exists during the run and is entirely gone after the timeout.

Two findings came from those fixtures during development and are fixed here
rather than described: the private tmpfs root was writable, and the first
reaping test asserted on namespace-local process ids that mean nothing on the
host. Both were defects in this work, not in the profile's design.

This is finite hostile-fixture evidence on one Linux kernel. It is not a
system-call filter, not hypervisor separation, and not an independent security
review.

full311-initial-failures.log retains an earlier run against a branch that
predated the Git-metadata fallback merged in #83; those four failures are that
known tooling issue, not an isolation defect.

Ubuntu CI on the first published head failed while macOS passed, and that
failure is the reason the probe changed. On that runner `unshare` succeeded but
writing `/proc/self/setgroups` returned `Permission denied`, because an outer
user namespace had already denied it and left the file read-only. Probing only
the first system call had therefore promised an isolation the caller never got.
The probe now performs the whole entry path, the already-denied state is
accepted as the state the setup wanted, and a setup failure after a positive
probe is reported as unavailability rather than returned as an unisolated
result.
