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
