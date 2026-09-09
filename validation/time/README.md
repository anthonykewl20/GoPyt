# Typed time validation

Runtime fingerprint:
`248bf8b8a9b960d489b85c970d061ffd8d26abbd52b10b0a7dfa87fe03c7739c`.
All 792 tests pass on Python 3.14.7 in 345.444 seconds. Nine focused compiled time
tests also pass on Python 3.11.16. They use the OS calendar as an independent
format oracle and inject wall-clock rollback, backwards/mixed monotonic samples,
clock failures and cancellation without changing the host clock. Exact endpoints,
pre-epoch values, offsets, invalid dates, precision loss and overflow are covered.

Guard calibration (17), stdlib parity (21 modules), contracts demo, wheel smoke
and installed upgrade/rollback pass. Two isolated wheel builds at epoch 1788998400
match SHA256 `7a420f8244ad984c2b1a15ce1ceaa042d6632439e91aff24b1f629e316e1e7d2`.
The runtime uses installed standard-library datetime; no dependency was added.

## Full source-date replay

Run `python tools/time_retail_probe.py '/path/to/Online Retail.xlsx' --output /tmp/time-new.json`.
The SHA-pinned UCI workbook and unused output path are required. Source/tool/runtime
identities, the exact conversion policy and acceptance conditions are frozen
before execution and checked again afterward. The independent oracle interprets
raw Excel decimal dates with exact Fraction arithmetic and the OS calendar;
runtime parsing uses integer datetime arithmetic.

All 541,909 rows pass parse/difference/reconstruction checks with zero mismatches.
The 23,260 distinct date cells are parsed through compiled GoPyt once each; all
adjacent row differences and reconstructions execute through compiled GoPyt.
438,708 rows required the explicitly declared nearest-nanosecond rounding of the
raw fractional serial; 528,201 results are not exact milliseconds. No raw decimal
precision is silently discarded. The report retains source identity and counts.

The workbook has no timezone. Fixed UTC is a test interpretation, not a fact about
historical transactions. There are 518,649 equal adjacent timestamps and zero
backwards adjacent timestamps in this dataset. It provides no real clock-fault
causation evidence. No throughput or wakeup-latency claim is made from this run.

## Real UTC discontinuity and explicit rejection

[IERS Bulletin C 52](https://datacenter.iers.org/data/16/bulletinc-052.txt) records
the 2016 leap-second sequence. Its source SHA256 is retained in
`iers-leap-replay.json`, alongside three transcribed UTC inputs and actual compiled
results. The surrounding ordinary markers are accepted; the leap-second marker
is rejected with ConvertError under the declared POSIX timestamp policy. This
covers a real announced discontinuity without pretending POSIX can represent it
or asserting that a leap second occurred on the test machine.

Reproduce with `python tools/time_leap_probe.py /path/to/bulletinc-052.txt --output /tmp/leap-new.json`.
The tool requires the exact bulletin hash and performs no download or clock
adjustment. The unit suite separately injects backwards wall/monotonic samples,
origin mismatch and clock read errors. These are controlled faults, not field
observations. This distinction and the dataset's absence of backward samples are
part of the qualification limits.

See the normative time amendment for the supported domain and deliberate
rejections. Combined numeric evidence remains in `validation/finite-f64/` and
`validation/money/`; whole architecture #17 completion still requires the merged
source and criterion review, including CI on supported systems.
