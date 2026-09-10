# Bounded heap-lock batch experiment

Status: candidate implementation for the PR #68 Linux/Python 3.11 inventory timeout.
It is not yet a qualified scheduling or performance guarantee.

Active stack samples put most observed workers at the heap or storage operation
locks. The candidate amortizes heap-lock handoffs across at most 32 bytecode
instructions. This changes the possible interleavings of parallel tasks, not the
language's guarantees for racing shared state. It does not make a batch atomic:
native calls and nested calls still relinquish the instruction lock.

Each instruction keeps existing cancellation checks, root adoption, automatic GC
checks, and opcode validation. The reusable frame guard holds exactly one physical
RLock acquisition across a batch. The existing released() scope drops and restores
that acquisition around native/nested work. Exceptions release immediately; a frame
finally block releases on return, malformed fallthrough, or any exceptional exit.
The public internal step helper defaults to one instruction for scoped callers.

Deferred resource cleanup runs only after physical lock release, at batch boundaries
or frame exit. Until then queued owners retain their charges and payloads. A batch
bound counts instructions rather than elapsed time; individual synchronous host
operations retain their documented limits. No deadline or client timeout is relaxed.

Before retaining this change, run the same inventory acceptance protocol and 10-second
request timeout used for the baseline and failed CI cases, plus lock ownership,
native release, exception/return, GC and resource teardown regressions. Preserve the
candidate and baseline source identities and unsuccessful experiments. Full Linux
and macOS validation on both Python versions remains required for publication.
