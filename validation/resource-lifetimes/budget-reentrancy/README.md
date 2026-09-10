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
