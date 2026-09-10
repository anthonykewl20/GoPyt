Tenant separation and the descriptor-check correction

No runtime source changed here: this is test and documentation work, so the
toolchain fingerprint and every lock pin are unchanged and no packaging
requalification applies.

Python 3.14.7 full language suite: 1,192 tests passed in 456.859s (full314.log).
Python 3.11.16 full language suite: 1,192 tests passed in 521.369s (full311.log),
with one test skipped because that run used an exported copy with no Git metadata.

## The descriptor check CI flagged

`gopyt.test_resource_mapping.test_each_acquisition_failure_releases_budget_and_descriptors`
failed once on ubuntu-24.04 / 3.14.7 during PR #85 with `9 != 8`, while the
identical job on the identical commit passed elsewhere. That was recorded on
issue #5 as an observation to investigate rather than left as an unexplained
re-run.

It was not a resource-lifetime defect. The assertion compared the size of
`/proc/self/fd` before and after an injected failure, which measures the whole
process: any other test's daemon thread opening a file moves that number while
saying nothing about the mapping path. `descriptor-count-sensitivity-probe.py`
reproduces it deterministically — with one unrelated thread opening and closing
`/dev/null`, the old assertion failed **173 of 400 trials**, while an
identity-based check found **zero** leaked mapping descriptors across the same
trials.

The assertion now identifies the leak directly: the backing memfd is named, so
`/proc/self/fd` is read for descriptors that still refer to a `gopyt-buffer`
mapping. It also asserts the budget's own descriptor usage returns to zero. A
companion test proves the check is not vacuous by observing exactly one such
descriptor while a mapping is live — mmap's duplicate, which the reservation
already accounts for — and none after close.

Under the same hostile descriptor churn the corrected assertion did not fail in
40 full-module runs. Two other tests in that module did fail under that churn:
they deliberately manipulate descriptor-number reuse, so a 10 kHz open/close
loop is hostile to them by construction. That is recorded here rather than
fixed; the real suite does not churn descriptors that way, and neither was the
test CI flagged.

## Tenant separation

Four tests qualify what concurrent evaluations can observe of each other: each
sees only its own `/work` scratch; a writable bind named by one caller is absent
from the other's mount namespace rather than merely unreadable; no procfs is
mounted, so a child cannot enumerate any process at all; one evaluation reaching
its own address-space ceiling leaves the other finishing normally; and reaping
one evaluation's process group on timeout does not reach a concurrent one.

This is separation between evaluations this runtime launches. It is not a
multi-tenant security boundary for hostile tenants, and the host kernel, a
caller-bound writable directory and the CPU remain shared.
