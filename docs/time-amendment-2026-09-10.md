# Typed timestamps, durations and monotonic samples

This normative extension to `core.time` addresses architecture #17. Existing
integer arithmetic remains checked. Three nominal records separate domains:
`Timestamp { unix_ns: i64 }`, `Duration { ns: i64 }`, and
`MonotonicInstant { ticks_ns: i64, clock_id: str }`. Record construction and
structural equality follow existing language rules; every time operation checks
its input record type, field count and integer domain. Host admission traps still
take precedence over domain-level `ConvertError` results.

## Representation and arithmetic

Timestamp is signed POSIX nanoseconds since 1970-01-01T00:00:00Z, excluding leap
seconds. Its exact bounds are 1677-09-21T00:12:43.145224192Z through
2262-04-11T23:47:16.854775807Z. Duration is a signed elapsed quantity with the same
i64 nanosecond bounds, roughly plus/minus 292 years. Nanosecond representation
does not promise a clock's physical resolution or accuracy.

`timestamp_ns`/`duration_ns` validate their coefficients. The `_ms` constructors
multiply by 1,000,000 and reject overflow. `timestamp_to_ms`/`duration_to_ms` reject
any nonzero nanosecond remainder, including negative values. There is no implicit
rounding or unit coercion. `add(Timestamp, Duration)`, `difference(later, earlier)`
and `duration_add` use exact integer arithmetic and return `ConvertError` on
result overflow. Timestamp differences may be negative; wall-clock adjustment
or reordered input is not silently clamped. Duration is elapsed time, not a
calendar month/day and not a timezone-aware scheduling instruction.

## Text and timezone policy

`parse_timestamp` accepts exactly `YYYY-MM-DDTHH:MM:SS[.fraction]offset`, using
ASCII digits, uppercase T/Z, 1..9 fractional digits when present and an explicit
`Z` or `+HH:MM`/`-HH:MM` offset. Gregorian calendar validation applies. Offset
hours are 00..23 and minutes 00..59. `-00:00` (unknown offset), leap second 60,
naive/local times, IANA timezone names, whitespace and excess precision are
rejected. Conversion applies the explicit offset and checks nanosecond range.
The deliberately restricted RFC3339 profile never guesses a DST fold or gap.
Applications must resolve timezone rules before constructing an instant.

`format_timestamp` always emits UTC with exactly nine fractional digits and Z.
Every representable Timestamp round-trips exactly. Offset spelling is not retained.
For example, `1969-12-31T23:59:59.999999999Z` represents -1 nanosecond.
`parse_duration`/`format_duration` use a canonical signed decimal integer plus
`ns`, such as `-1500000ns`; no plus, leading zeros, negative zero or other units.
Use these strings with external JSON consumers that cannot preserve i64 integers.
Language typed record serialization retains integer fields exactly, subject to
the existing consumer/JSON limitations. No float timestamp conversion is added.

## Clocks, identity and waiting

`now()` returns an integer `time_ns` wall-clock sample as Timestamp or an explicit
error. It may move backwards or jump forward. Use `monotonic_now()` for elapsed
measurements; each VM assigns its samples a random 32-lowercase-hex origin ID.
`elapsed(later, earlier)` requires matching origins, nondecreasing ticks and an
i64 result. It returns an error on mixed origins, backwards samples or overflow.
This function is pure: it compares the provided records and does not read a clock.
A pair from a single origin may be used for offline arithmetic; samples from a
new VM cannot be mixed with the old pair. Origin IDs are provenance labels, not
unforgeable capabilities: public record construction can invent samples. They
provide neither authentication nor a way to compare clocks across machines.
No wall/monotonic conversion or cross-origin ordering is defined.

`sleep(Duration)` rejects negatives, uses an integer monotonic deadline, checks
ancestor cancellation, and waits in host chunks no longer than 50 ms. It returns
an error on a backwards clock or host clock/sleep failure. It rechecks deadlines
instead of assuming one host sleep slept long enough. Scheduling, suspend behavior
and clock resolution follow the host monotonic clock; elapsed time is not a
promise of real-time wakeup latency. Cancellation remains cancellation, not a
successful result or a conversion error. Domain/host clock errors return
`core.status.ConvertError` for the new APIs.

Legacy `sleep_ms(i64)` retains its full nonnegative i64 millisecond range and
existing cancellation behavior; not every legacy duration fits a typed Duration.
Legacy `now_ms()` now computes `time_ns() // 1_000_000`: it explicitly floors to
the containing Unix millisecond, including pre-epoch samples, avoiding float
precision loss. Host clock failure or noninteger samples trap with code 9; an
out-of-i64 millisecond result traps with overflow code 3. Its old return signature
remains i64. This fixes the previously unspecified pre-epoch/precision boundary.

## Evidence and references

Compiled tests use the POSIX C calendar (`time.gmtime`) as an independent formatting
oracle; the implementation uses integer datetime/timedelta arithmetic. Tests cover
both range endpoints, pre-epoch nanoseconds, explicit offsets, leap/calendar
rejection, exact unit conversion, overflow, wall-clock rollback, mixed origins,
backwards monotonic samples, host failure and cancellation. Fault injection tests
clock anomalies without changing the machine's clock. Real-data timestamp order
regressions do not by themselves establish a system-clock fault.

References: [RFC3339](https://www.rfc-editor.org/rfc/rfc3339) for offset/unknown-offset
semantics and the [Python time reference](https://docs.python.org/3/library/time.html)
for integer clock interfaces and host limitations. The implementation's explicit
profile/range restrictions above take precedence over broader reference features.
Leitir's sampled-verified CPython reference at
`823f0323ee6ec1402088b73bce1a38473cac36dc` was read for `_pydatetime.py` calendar and
formatting behavior and its PSF license history. No donor code was copied or
executed, and no runtime dependency was added.

[Retained validation](../validation/time/README.md) includes the full suite,
full date-cell replay, IERS marker replay and their explicit limitations.
