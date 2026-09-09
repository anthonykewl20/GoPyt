# Native boundary amendment: 2026-09-09

This amendment resolves audit findings R1–R3 from the
[architecture checklist review](architecture-checklist-review-2026-09-09.md).
It changes no syntax, native signatures, bytecode version or effects.

## Sleep and cancellation

`core.time.sleep_ms` accepts every nonnegative i64 millisecond duration. Negative
inputs continue to trap on the declared precondition. Zero returns `unit`
without a host wait. A positive sleep completes only after its elapsed duration,
measured with an integer monotonic clock; wall-clock changes do not shorten it.

The implementation waits in slices of at most 50 ms and checks all inherited
cancellation events between slices. Cancellation unwinds through the existing
structured-task mechanism, including its join and cleanup behavior. An i64
maximum duration therefore neither overflows a host timeout nor prevents
cancellation. The slice bounds the requested host wait, not OS scheduling delay
or total application shutdown latency. Other blocking natives retain their
existing cancellation boundaries; this is not a shared end-to-end deadline.

## Optional local model integration

If importing the optional local provider fails with `ImportError` (including
`ModuleNotFoundError`), `core.model.local` returns its declared `ModelError`.
The import and provider invocation share the existing typed-error boundary.
No external-provider fallback or provider installation is performed. Existing
provider value, I/O, runtime and Unicode errors retain their error behavior.

## Malformed operator policy

Excessively nested DB policy JSON that exceeds the host parser recursion limit
is invalid policy. It raises the internal `SecurityError`, normalized by storage
to `StorageError` and by the DB native to `DbError`, before cache/data access.
This matches schema-invalid policy behavior on runtimes whose JSON parser can
parse deeper nesting. No grant is inferred from malformed configuration.

## Regression evidence and remaining scope

`gopyt/test_native_boundaries.py` exercises compiled GoPyT calls: absent provider,
real deeply nested policy, injected parser recursion failure, maximum-duration
sleep with ancestor cancellation and subsequent VM reuse, real host wait with
cancellation, and minimum elapsed sleep. Injection tests cover deterministic
boundaries; real host tests cover execution without substituted waits/parsers.

The original failing regression run is retained in
`validation/runtime-boundaries/before-python314.log`. Historical audit evidence
is unchanged. The latest test logs are retained alongside it. These fixes advance
issues #4, #13, #17 and #22. They do not close delegation, numeric/time API,
concurrency qualification, or compatibility/upgrade requirements.
