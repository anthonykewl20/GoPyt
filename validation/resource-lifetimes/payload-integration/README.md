# Payload integration qualification in progress

The initial full Python 3.14.7 run completed 1016 tests in 355.691 seconds with six
failures. Four serialization fault-injection subcases still patched encode_bytes,
although these paths now call encode_owned_bytes; the two diagnostic coverage
failures repeated the same underlying tests. The raw failure log is retained.
The injections now target the active encoder without changing deadline, cancellation,
no-transport or no-success-response assertions. Both pinned Python versions passed
all 11 targeted tests after this correction. The full Python 3.14 rerun passed; its final result is retained in full314.log.
Full Python 3.11 qualification and exact-head platform CI remain pending.

The runtime source did not change for this test correction. Initial source hashes,
40 passing focused Python 3.11 tests, reproducible-wheel checks, installed smoke,
upgrade/rollback, Guard calibration, stdlib parity and the contract demo are retained.
These are partial qualification evidence, not completion of issue 5. Decoded values,
escaping and parser temporaries, transport internals, SQLite/TLS/encryption allocator
coverage and additional native paths remain incomplete.
