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
