# Decoded payload accounting boundary

Source review follows restore admission at 89ef0a7. Remaining concrete producers
include core.bytes.to_str, core.bytes.from_str, core.str.concat and slice,
core.bytes.concat, model-response UTF-8 decoding, and jsonc.parse. Charging a byte
input alone does not account for its decoded string, parser results or explicit
copies. core.str.concat currently allocates UTF-8 copies just to check MAX_ALLOC.

Implement owned string payloads with reservations attached to the actual string
object, using the same lock-free finalization protocol as charged bytes. Admit
capacity before decoding and before creating an owned copy; retain overlapping
raw and owned capacity until the raw reference is cleared. A conservative Unicode
capacity bound must cover up to four bytes per code point; Python object metadata
and allocator overhead remain distinct from payload capacity. Check cancellation
before and after synchronous conversion, without claiming intra-call preemption.

Audit exception objects as well as local aliases. UnicodeDecodeError may retain
input bytes; retained tracebacks must not outlive a released temporary reservation.
Return the existing typed conversion error only after conversion temporaries are
cleared, and do not retain an uncharged original exception through implicit chaining.
Verify borrowed input aliases separately from any exception-owned input copies.

Avoid encoded temporary strings when measuring UTF-8 size. Concatenation, slicing
and encode/decode output must reserve before production and preserve output charges
through VM/host aliases. Validate existing isinstance-based string handling,
equality, hashing, map keys, canonical JSON and native boundaries before integration.

JSON needs a separate parser allocation policy covering strings, containers,
Decimal/numeric parsing, pair lists, duplicates and typed conversion copies. Wrapping
only the final result after json.JSONDecoder returns is not admission. Keep this gap
explicit until the producer itself is bounded or isolated with qualified limits.

Required tests include zero-budget rejection before conversion, invalid UTF-8 with
retained errors, astral code points, empty values, cancellation, retained aliases,
copy overlap, GC finalizer reentrancy, and language-level roundtrip/concat/slice
semantics. This design does not implement or qualify those boundaries yet.
