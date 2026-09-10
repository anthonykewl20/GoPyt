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

core.bytes.to_str now uses the owned UTF-8 helper: capacity rejection raises the
existing allocation trap, invalid UTF-8 returns the existing conversion error,
and successful strings retain their charge through aliases. The charge currently
retains its conservative input-derived capacity rather than shrinking to character
width. Other string/byte producers and JSON parsing remain unfinished. The 26-test
resource/native selection passes on both pinned runtimes; full language integration
and remaining producer/fault qualification are pending.

core.bytes.from_str now uses ByteBuilder admission before encoding and its owned
final byte payload. Cancellation is checked before and after encoding, capacity
rejection maps to the allocation trap, and output aliases retain the byte charge.
The 29-test text/byte/native selection passes on both pinned runtimes, including
multibyte output and rejection before an instrumented encoder is called.
Concatenation, slicing, model decoding and parser allocations remain unfinished.

core.bytes.concat now admits mutable scratch and final immutable capacity before
copying borrowed inputs through a writable memoryview. Its existing MAX_ALLOC
limit remains; resource rejection maps to the allocation trap. Output aliases
retain their charge. The 31-test text/byte/native selection passes on both runtimes,
including two-output-size peak capacity and failed output admission cleanup.
String concatenation/slicing, model decoding and JSON remain separate work.

core.str.slice now admits temporary and owned Unicode capacity before copying a
validated code-point range. Existing index preconditions and range conversion errors
remain; capacity failure raises the allocation trap. Each capacity uses four bytes
per output code point plus a terminator, retained conservatively through aliases.
The 22-test text/native selection passes on both runtimes, covering empty/full and
multibyte slices plus admission failure cleanup. Full qualification remains pending.
