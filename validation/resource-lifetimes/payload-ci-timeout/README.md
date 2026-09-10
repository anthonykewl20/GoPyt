# Payload CI timeout investigation

PR #70 head aa3e8f32798b40fd375d711a03d46969c5cfb156 passed seven platform
checks, but the PR Linux Python 3.11 job exceeded the unchanged 15-minute
regression-step timeout. The raw log is retained. Captured nested-test output
prevents locating the stalled test from this log. No cause is established yet.

The CI launcher now emits all-thread stacks every 120 seconds while preserving
unittest arguments, exit status and the existing timeout. This is diagnostic
instrumentation, not a timeout fix or qualification success.
