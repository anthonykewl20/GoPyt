# Budget finalizer reentrancy probe

A controlled Python 3.11.16 subprocess collects a cycle containing charged
bytes while the budget lock is held. It stalls in ResourceBudget._release
from _ChargedBytes.__del__; a one-second faulthandler dump identifies the
stack and the parent terminates this dedicated probe after three seconds.

Budget methods allocate Python objects while holding this non-reentrant lock,
so GC finalization is a relevant boundary. This probe establishes that the
lock cannot tolerate such finalization; it does not prove that this caused
the earlier CI timeout or the separate live storage suite.

A fix must also preserve counter updates during reentrancy: changing Lock to
RLock alone could permit a nested release to be overwritten by an outer
precomputed counter update. Neither disabling GC globally nor raising CI
timeouts establishes correct ownership.

The candidate fix marks finalization using pre-existing token/budget fields.
It performs no lock acquisition or queue allocation in payload finalizers.
Budget admission, release, reduction and snapshots drain marked reservations
under the ledger lock. Marks arriving during a counter update cannot overwrite
that update; they are drained at a subsequent boundary. Until drained, charges
are conservative. Explicit release retains synchronous behavior.

The controlled probe now completes and verifies zero remaining charges. A second
regression forces collection during admission and verifies that only the new
reservation remains. Both pinned runtimes passed 17 focused tests. Broader
qualification and attribution of the CI stall remain pending.

Broader resource/HTTP/model regression passed 51 tests on each pinned runtime.
An injected counter-update MemoryError retains the charge and pending mark;
the next snapshot retries successfully. All three finalizer regressions pass
on both versions. Full-suite qualification remains pending.

The candidate runtime wheel reproduced byte-for-byte (SHA-256
`294532c9631e95db59139b32eae9863aaf27c2352e22892901944eff083f68de`,
57 runtime files). Installed smoke and old/new/rollback checks passed.
Full regression qualification remains running; packaging does not establish
resolution of either CI failure.

Guard calibration passed 17 tests, stdlib parity passed for 22 modules, and
the contract demo passed for the candidate runtime. Outputs are retained.

Full Python 3.11.16 qualification passed 1019 tests in 418.351 seconds.
Frozen source and lock hashes were verified after completion. Scheduled
thread dumps are retained in the raw log. Full Python 3.14 remains pending.

Diagnostic CI head 88cd18dbf3b5349c1468d1aa7eac456775ff1b29 subsequently
timed out in both macOS Python 3.11 jobs. Job 102748720807 repeatedly shows
_ChargedBuffer.__del__ -> reservation.release -> ResourceBudget._release
nested inside ResourceBudget.reserve, confirming the finalizer lock deadlock
in an actual regression run. Both raw logs are retained. This does not
establish the cause of the separate Linux segmentation fault.
