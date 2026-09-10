# Integer formatting and canonical JSON output admission

`core.str.from_i64` and JSON integer tokens admit decimal-conversion scratch and
owned ASCII text before formatting. Signed i64 and u64 outputs need at most 20
characters plus the terminator. The supported CPython profiles use at most
`1 + 2 * ceil(64 / bits_per_digit)` native digit cells for this decimal scratch.
The raw and owned Unicode payloads are charged separately. Formatting calls the
integer implementation directly; no user-defined formatting hook is introduced.

The owned JSON encoder admits each bounded input slice, escaped quoted string,
and quote-stripped string before construction. For n input code points, the
reservations cover four bytes times `(n + 1)`, `(6n + 3)`, and `(6n + 1)`
respectively. The escaping function is CPython's `_json.encode_basestring`, whose
output is sized before allocation. Calling this C entry point avoids Python
wrapper traceback frames retaining raw temporary slices after a failed call.
Tokens are copied into the existing admitted byte builder before temporary
string reservations are released. Map key ordering uses the admitted collection
sort without encoded-key copies.

VM `data.json.encode` and generated `Json.to_json` calls use this encoder and
return an owned Unicode result. The intermediate canonical UTF-8 output and final
Unicode decode overlap under separate reservations. Text aliases retain the
final charge after VM close. The existing byte-count ceiling and JSON grammar,
member ordering, escaping, width checks and opaque-value rejection remain in
force. Resource exhaustion maps to the allocation trap. Non-VM host encoding
without a budget keeps its existing API; that path is not budget enforcement.

Cancellation is checked at encoder boundaries and between bounded escaping
chunks. Host decimal conversion, escaping and individual byte/Unicode copies
are synchronous. Object headers, encoder/ledger metadata and allocator overhead
are outside payload counters. This increment does not qualify aggregate RSS or
SQLite/TLS/cryptographic allocation behavior.

Sizing references are the pinned CPython 3.11.16 and 3.14.7 `Objects/longobject.c`
(`long_to_decimal_string_internal`) and `Modules/_json.c` (`escape_unicode`), plus
the previously reviewed Unicode subtype copy and UTF-8 decode paths. These are
read-only references, not new dependencies. Focused tests retain failure and
cancellation evidence, compare canonical output to the host JSON oracle, and
exercise compiled natives and generated trait conversions. Full and platform
qualification remain pending for this increment.
