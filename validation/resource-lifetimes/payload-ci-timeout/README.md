# Payload CI timeout investigation

PR #70 head aa3e8f32798b40fd375d711a03d46969c5cfb156 passed seven platform
checks, but the PR Linux Python 3.11 job exceeded the unchanged 15-minute
regression-step timeout. The raw log is retained. Captured nested-test output
prevents locating the stalled test from this log. No cause is established yet.

The CI launcher now emits all-thread stacks every 120 seconds while preserving
unittest arguments, exit status and the existing timeout. This is diagnostic
instrumentation, not a timeout fix or qualification success.

The push Linux Python 3.11 job on diagnostic head
88cd18dbf3b5349c1468d1aa7eac456775ff1b29 exited 139 while printing a
120-second thread dump during test_generated_state_sequences. Its partial
stack stops at vm.py. The full job log is retained. This is a separate
failure from the earlier 15-minute timeout; neither its cause nor a common
cause is established. The diagnostic launcher is not yet qualified.
