Operations and review-package evidence

This change adds documentation, a freeze revision and consistency tests. No
runtime source changed, so the toolchain fingerprint and every lock pin are
unchanged and no packaging requalification applies to it.

Python 3.14.7 full language suite: 1,187 tests passed in 513.116s (full314.log).
Python 3.11.16 full language suite: 1,187 tests passed in 584.131s (full311.log),
with one test skipped because that run used an exported copy with no Git metadata.

The consistency tests are the point of the change, not decoration. They caught a
wrong module path in the review package (`gopyt/storage_rollback.py`, which does
not exist) before it reached a reviewer, and they hold the runbook's recovery
objectives, supported platforms and named ownership to the frozen targets rather
than to prose.

The freeze moved to revision 2 to remove issue #24 from its known gaps after
that issue closed. No target, threshold, dataset identity or envelope value
changed; the revision reason records exactly that, and a test asserts a removed
gap carries it.
