# JSON admission boundary review

Reviewed gopyt/jsonc.py at runtime 50589c241a1e079effc2a6dd29b60f97f0d31037c55daa3e306d6295bc45348e.
parse calls json.JSONDecoder before any aggregate resource admission. Its object_pairs_hook
receives an already allocated pair list, constructs a dict and rejects duplicates.
parse_float=Decimal creates numeric objects; raw_decode constructs all nested strings,
containers and integers. lstrip and trailing slicing can copy input. Post-parse UTF-8
validation encodes every string, while pending.extend allocates traversal storage.

Typed _dec then constructs replacement lists/maps and nominal records, optional
wrappers and typed integers while the generic graph remains alive. Charging only
source text or adopting final values after _dec cannot admit these producers.

The next implementation needs a context propagated through parse/decode, explicit
admission before each producer, and ownership of temporary and returned graphs.
A producer-controlled parser can reject duplicate keys before adding duplicate
values, validate Unicode escapes/surrogates without encode copies, enforce depth,
check cancellation and charge string/container capacity before construction.
It must preserve strict JSON numbers (Decimal), integer range conversion, duplicate
key rejection, whitespace/trailing syntax and existing union/nominal rules.
Typed conversion must charge its overlapping destination graph before allocation.

Tests must compare with an independent JSON oracle for accepted values and cover
malformed escapes, surrogate pairs, duplicate keys, huge numeric exponents, depth,
partial graph cleanup, cancellation, retained aliases and exceptions, and concurrent
budget pressure. Rejecting previously valid JSON solely to simplify accounting is
not an acceptable compatibility shortcut. No parser change is implemented yet.

Initial internal string_token implementation scans the quote boundary without a
substring, admits owned Unicode capacity and scanner temporary capacity, invokes
the strict string scanner and rejects non-scalar Unicode without encoded copies.
The returned string retains its reservation. Two focused tests pass on both pinned
runtimes. This helper is not yet connected to parse/decode; complete scanner-bound
review, container/numeric producers, typed conversion overlap and failure coverage
remain required before qualification.

Scanner review: matching CPython release Modules/_json.c uses a Unicode writer
(3.11 explicitly enables overallocation; 3.14 creates a public writer). The
writer may overlap old/new storage; substring writes copy directly into its
capacity. JSONDecodeError computes line/column via count/rfind on the original
document, without constructing a prefix slice. Object metadata and small formatted
error messages remain outside payload-capacity accounting. Four focused tests pass
on both runtimes, including escape-heavy widening and a malformed token after a
100000-character prefix with retained traceback, no chained scanner exception,
and released token reservations. This finite evidence does not complete parser
or typed-result allocation qualification.

number_span now separates strict JSON number grammar from allocation: it scans
ASCII digits, signs, fractions and exponents without slicing or converting, returns
the end offset and integer/Decimal classification, and checks cancellation every
4096 characters. Delimiter validation belongs to the surrounding parser. Seven
string/number tests pass on both runtimes, with offsets compared to JSONDecoder
and long-scan cancellation retaining a traceback whose input reference is cleared.
Numeric object allocation and parser integration remain unimplemented.

Numeric ownership probe: integer and Decimal subclasses can retain an owner
attribute, preserve equality/hash and finalize only after the last alias on both
pinned runtimes. Cases include a large integer, 1.25 and Decimal 1e1000000; this
does not establish allocation bounds. Matching CPython 3.11 source shows Decimal
Unicode construction allocating numeric_as_ascii scratch, constructing the decimal
and only then freeing scratch. Integer conversion transforms Unicode digits before
PyLong_FromString; decimal-base parsing and subclass copying need separate review.
Numeric admission must cover these overlaps before invoking constructors, preserve
the runtime integer-digit limit and Decimal exponent semantics, and discard errors
inside the temporary reservation. No numeric producer is integrated yet.

Integer producer: matching CPython 3.14 longobject.c switches decimal conversion
above 6000 digits to _pylong when the configured digit cap permits it. Consequently
a bound derived only from the 3.11 decimal constructor is insufficient. The new
internal integer_token instead converts at most nine ASCII digits at once, then
multiplies/adds into the result. It preserves sys.get_int_max_str_digits rejection
before allocation and permits larger inputs when that limit permits them.
Reservation capacity is sizeof_digit*(decimal_digits+1), conservatively one binary
limb per input digit plus carry. Three such capacities cover old/product/sum
intermediates, with ten bytes for the chunk string including terminator and four
limbs for chunk/multiplier; the final integer subclass has a separate reservation.
The multiplier is bounded by 10**9, so multiplication has a small operand rather
than invoking the unrestricted decimal-string conversion algorithm. This counts
payload capacity, not Python object headers. The chunked algorithm needs large-input
performance qualification before integration, particularly with disabled digit caps.
Ten focused token tests pass on both pinned runtimes. They compare integer values
and hashes to json.loads, retain aliases, reject zero budgets and configured digit
limits, and retain cancellation tracebacks while verifying released reservations
and cleared producer references. Decimal construction, container ownership, typed
conversion and parser integration remain outstanding.

Large-integer follow-up: integer_probe.py temporarily disables the digit cap in
its own process and compares negative 6001-, 20000-, and 100000-digit values with
json.loads on both pinned runtimes. All values/hashes/offsets match and final
reservations release. At 100000 digits the chunked path took about 0.084/0.087s
(3.14/3.11), versus oracle 0.0093/0.028s in this finite run. This is a measured
performance cost, not a general bound or benchmark guarantee. The probe restores
the prior digit cap in finally. Eleven focused tests now pass on both runtimes,
including cancellation after two conversion chunks and constructor MemoryError
with the traceback retained and all producer payload references cleared.

Decimal source review continues: 3.11 PyDecType_FromCStringExact creates the
requested subtype directly, invokes mpd_qset_string with maxcontext, and translates
status afterward. The coefficient length is ceil(significant_digits/MPD_RDIGITS),
not exponent-expanded length. mpd_qresize clamps to MPD_MINALLOC; Decimal embeds
four coefficient words (_Py_DEC_MINALLOC), switching to dynamic allocation when
needed. numeric_as_ascii adds a len+1 scratch allocation. The finalization/status
paths and corresponding 3.14 implementation still need complete bounds before
adding the admitted Decimal producer.
