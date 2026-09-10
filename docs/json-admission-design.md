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

Decimal producer added internally: both matching release context.c implementations
set maxcontext round=HALF_EVEN and clamp=0. Thus _mpd_check_exp does not take the
fold-down expansion or directed-rounding max-coefficient paths; underflow shifts
right in place. Exact construction translates Rounded/Inexact/Clamped to invalid
operation. The new decimal_token reserves 8*(token_length+4) owned bytes (one
maximum-width coefficient word per token character plus four embedded words),
another coefficient capacity for resize overlap, and two token_length+1 buffers
for the ASCII token slice and numeric_as_ascii scratch. Decimal constructs the
subtype directly; there is no intermediate Decimal-to-subclass copy. Object
headers and small exception metadata remain outside payload accounting.
Fifteen focused tests pass on both pinned runtimes. Decimal tests preserve signed
zero/coefficient tuples, large positive/negative exponents, hashes and alias
ownership; reject insufficient capacity before the constructor; release on invalid
exponents and cancellation after construction; and preserve InvalidOperation flags
and NaN behavior when the caller disables that trap. These are token-producer
checks, not complete JSON parser or typed-graph qualification. Parser integration,
container admission, more constructor fault coverage and broad validation remain.

Array producer: _JsonArray is an internal append-only parser container, not a
replacement for mutable language lists. Its append_owned operation reserves a new
pointer array before growth, retaining the old reservation until append returns.
Matching 3.11/3.14 listobject.c uses (n+(n>>3)+6)&~3 slots for single-element append;
no resize reservation is needed while those slots remain available. Pointer size
comes from the running interpreter. Empty list headers are metadata, not payload.
This release boundary targets the pinned GIL-enabled runtimes: free-threaded 3.14
has deferred array reclamation and is not qualified by these tests or ownership
rules. External mutation methods must not be used on this parser-private object;
typed conversion must admit and own the mutable language destination separately.
Eighteen focused tests pass on both pinned runtimes. Array tests compare charged
slots with list.__sizeof__ growth for 1000 appends, retain a last alias, reject a
resize whose old/new overlap exceeds the budget while preserving prior contents,
and retain cancellation traceback after successful insertion without retaining the
array through the producer frame. Object/dictionary admission and actual parser
integration remain outstanding.

Object producer: _JsonObject is parser-private and insert-only. Matching release
hash tables use two-thirds usable slots and growth based on used*3; without
removals the required power-of-two table doubles at each threshold. General
entries store hash/key/value, while indices are at most pointer-width. Four
pointer words per slot conservatively admit this storage (including unused slots).
Every insertion reserves the new table bound while retaining the old reservation,
even without growth, because a string subclass key can switch a Unicode-only table
to general entries. Duplicate insertion rejects before reserving or mutating.
Twenty focused tests pass on both pinned GIL runtimes. A 1000-key mixed exact-str/
subclass-key probe compares actual dict.__sizeof__ growth with the charge; aliases
retain ownership and duplicate/capacity rejection preserves prior contents. These
finite tests do not qualify free-threaded delayed reclamation, arbitrary mutations,
or complete parser/typed-graph behavior. Containers remain internal and unintegrated.

Internal parser integration: parse_owned uses the admitted scalar and container
producers with linked metadata frames, avoiding a Python recursive descent call
per nesting level and avoiding an uncharged list stack. Whitespace scans do not
slice input, literals use startswith offsets, object keys reject duplicates before
producing their values, and trailing commas/input reject. Frame/container/key/value
references clear on parser exit. Twenty-two focused tests pass on both pinned
runtimes: nested values agree with the JSON/Decimal oracle and malformed partial
graphs release their reservations even while exceptions remain live. Production
jsonc.parse is unchanged. Remaining qualification includes cancellation at parser
boundaries, retained child aliases, depth compatibility and frame-allocation faults,
then typed conversion ownership and native routing. Linked frame/object headers
are metadata excluded from the payload ledger; this is not whole-heap accounting.

Parser failure qualification: _Frame.__init__ now clears its constructor references
in finally so constructor tracebacks do not retain parent/container arguments.
Twenty-five focused tests pass on both pinned runtimes. A nested document is first
parsed to count checks, then cancelled at each reached check with the exception
retained; each run releases all reservations. Budget limits from 0 through 1989
in steps of 17 exercise partial allocation failures with retained exceptions and
also release. A child array retained after its parent dies keeps only its remaining
graph charged and releases on the last alias. These finite sweeps do not cover all
inputs or constructor failure points. Production _dec still uses list/dict
comprehensions, Some and typed integer wrappers; those destination producers need
admission while the parsed graph remains alive. Existing decode catches recursive
typed-conversion failure as depth; parser depth compatibility remains to qualify.

Typed-result integration review corrected the earlier mutation assumption: native
core.list.append/core.map.set and VM LIST_APPEND/MAP_SET create copies rather than
mutating their inputs. Those copy producers remain separate accounting work.
Existing value_eq rejected owned list/dict/int subclasses at its exact-Python-type
gate. It now compares lists and maps structurally before that gate, and integers
by scalar_type_id plus value, preserving bool and declared integer-width union
members. Map comparison uses length/membership instead of temporary key sets.
The focused JSON and VM suites pass on both pinned runtimes, including owned/plain
nested graph equality in both directions and cross-width rejection. This removes
one integration obstacle; typed destination producers and production routing are
still pending.

Typed integer destinations: integer_value admits a width-preserving owned wrapper
for existing integer tokens after range validation. The bound is enough binary
limbs for 64 bits, with a second capacity reserved for subtype conversion scratch.
Direct I32/U32/U64 subclasses preserve scalar_type_id and structural equality.
An initial multiple-inheritance attempt loaded on 3.14 but failed with an instance
layout conflict on 3.11; both raw logs are retained. The corrected constructors
pass all 27 focused tests on both runtimes, including each width's lower/upper
bounds, bool rejection, cross-range rejection and alias lifetime. Decimal inputs
are not handled by this helper: 3.14 dec_as_long allocates a rounded mpd temporary
and, above signed i64, an exported-digit buffer overlapping PyLongWriter storage.
Those paths need admission before typed decoding can route Decimal inputs here.

Decimal-to-integer follow-up evidence: matching 3.11 dec_as_long always calls
mpd_qexport_u32/u16 after rounding into a new mpd object; matching 3.14 first tries
mpd_qget_i64 and otherwise exports. mpd_qexport_u32 allocates the binary digit
buffer using mpd_sizeinbase, creates a shifted coefficient temporary, and invokes
base conversion before the final Python integer copy/writer. Bounds must include
that overlap; the owned destination alone is insufficient. Source locations for
remaining review in matching 3.14 mpdecimal.c: mpd_qshiftr at 2635, mpd_sizeinbase
at 8085, _baseconv_to_smaller at 8221, export at 8461.
The new decimal_integer_probe runs 12 finite cases on both pinned runtimes,
including signed/unsigned 64-bit boundaries, fractional rejection, huge exponents,
and a 100000-zero fractional suffix. Results and rejection messages agree across
runtimes. Tracemalloc starts after the input Decimal is constructed; measured peaks
are observations only and do not prove coverage of every libmpdec/native allocation.
No production routing or Decimal-to-integer reservation bound is claimed from this
probe. Runtime files are unchanged by this evidence increment.

Typed Decimal conversion helper added: decimal_integer_value reserves two times
Decimal.__sizeof__(input)+8 for to_integral_value destination/resize overlap;
rounding copies or right-shifts the original coefficient and may add a word.
The rounded temporary is discarded before releasing this reservation. Existing
integrality and range checks precede int conversion, preserving rejection messages.
The bounded export then has at most 20 decimal digits: two 8*(20+4) coefficient
capacities cover rounded/shifted mpd storage, and two 4*20 digit capacities cover
binary export overlap (one maximum-width word per decimal digit). integer_value
separately admits the final typed object and subtype-copy scratch. Temporary raw
integer and Decimal references clear before their reservation scopes exit.
Twenty-eight focused tests pass on both runtimes. Forty source/type combinations
compare results or exact rejection messages against existing _as_int plus type
ranges, including u64 boundaries and long fractional-zero coefficients. Production
typed decoding is still unchanged; helper cancellation/fault and broader graph
integration qualification remain required.

Typed numeric failure coverage: Decimal integrality now invokes the base Decimal
method and checks cancellation while the rounded temporary remains admitted.
Thirty focused tests pass on both runtimes. Each reached check in integer and
Decimal u64 conversion is cancelled with the failure retained, and every budget
limit from 0 through 599 bytes exercises temporary/destination admission boundaries.
All reservations release after returned values or failed conversions are dropped.
These finite cases supplement source bounds; complete typed graph decoding remains
unimplemented and production JSON routing remains unchanged.

Internal typed graph integration: decode_value now dispatches the existing type
rules through owned integer/Decimal producers and owned destination list/map
containers. _nominal_value builds admitted field/name pointer arrays and preserves
record/enum field validation; optional/record/enum object headers are metadata.
Strings and map keys retain parsed ownership by alias. Thirty-one focused tests
pass on both runtimes; the first nested map/list/optional graph compares with _dec,
observes simultaneous source/destination charges, and verifies independent release.
Nominal/union/error/cancellation cases still need qualification. Artifact const_str
and _member_name can produce text outside this helper's current admission boundary;
that must be resolved before claiming complete typed payload coverage. Production
routing remains unchanged, and this internal recursive typed traversal has not yet
been depth-qualified.

Artifact-name review resolved the suspected allocation: Artifact.const_str returns
c.value directly, and _member_name returns that borrowed string or a scalar literal.
No text producer is invoked there. Nominal/union tests now compare records, enums,
optional fields, unknown/missing fields and variants with _dec. Cancellation at
each reached nominal conversion check retains only the borrowed parsed graph's
charge; dropping that graph releases all reservations. _matches now recognizes
integer subclasses by declared width instead of exact Python class, preserving
bool rejection and width distinctions for union encoding. Thirty-three focused
tests pass on both runtimes (nominal-match logs). Production routing and broad
encoding/depth/failure qualification remain outstanding.

Production typed decode routing is now connected: jsonc.decode accepts an optional
budget/check pair and uses parse_owned plus decode_value when supplied. Its wrapper
clears the parsed graph and input references on exit, translating recursive typed
traversal failure to depth after discarding the original exception. The native
data.json.decode supplies the VM shared budget/check and converts ResourceLimitError
to TRAP_ALLOC; provided Json.from_json delegates through that same native. Existing
host calls without a budget retain their prior path. Focused JSON/native-boundary/VM
suites pass both pinned runtimes; 34 focused resource JSON tests include a direct
native success with retained result charge and zero-budget TRAP_ALLOC/release.
Full language suites, depth compatibility and model-response JSON routing still
need qualification; no issue closure is claimed by this connection alone.

Model-response routing now uses _model_response_text with parse_owned and the
request's remaining-deadline check. It returns the owned text alias, validates the
single required key without a temporary set, translates budget rejection to
TRAP_ALLOC and preserves ModelError decode for invalid protocol/JSON. Both helper
and caller clear response references on exit. Focused resource JSON/native-boundary
tests pass both pinned runtimes, including retained text ownership, invalid-response
cleanup and zero-budget allocation traps. Full integration/TLS and depth validation
remain required before release qualification.
