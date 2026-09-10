OS isolation profile evidence

Frozen source/runtime identity is in source.json (head 5105618, runtime
sha256:608559e1fb2478b5c0b4ee54aab3eb6d8e0c7ec75e89c1d20a4d1ef0f822b8fb,
595 file hashes). profile.json records what this host could actually install
and, importantly, what the profile does not provide.

Python 3.14.7 full language suite: 1,176 tests passed in 506.091s (full314.log).
Python 3.11.16 full language suite: 1,176 tests passed in 564.514s (full311.log),
with one test skipped because that run used an exported copy with no Git metadata.

Two wheel builds under SOURCE_DATE_EPOCH=1788998400 are byte-identical
(wheel SHA256 6344a7c036abc2e91fc1f49c7a9fcd138963da1adb7d95fac90b82a196095dc7,
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
