# Collection allocation admission

List construction, persistent append, integer ranges, map construction, persistent
map updates and sorted map keys share the VM native-byte budget. This covers the
named native paths and `NEW_LIST`, `LIST_APPEND`, `NEW_MAP`, and `MAP_SET` bytecode
instructions. Budget exhaustion raises the existing allocation trap. Element-count
limits remain in force. This is payload-capacity accounting, not an RSS limit.

An owned list admits its pointer-array capacity before each append that grows it.
During growth, both old and replacement capacities remain charged until append
returns. An owned map similarly admits a replacement combined-table capacity
before insertion or replacement, including an equal key whose representation may
change a Unicode-specialized table into a general table. Map copies build a fresh
admitted destination; the source and its borrowed keys/values remain unchanged.
JSON parsing and typed conversion use these same builders, retaining their own
string-key and duplicate-key rules.

Ranges admit generated i64 payloads individually. A scalar alias can outlive the
list and retain its own charge. Two maximum-i64 digit capacities cover simultaneous
old/new loop-counter arithmetic; no unadmitted intermediate range list is built.
Copies borrow existing element payloads and admit their own pointer storage.

Key enumeration builds an owned list and sorts it in place without a key function.
Sorting admits one pointer per element as conservative temporary merge capacity.
Valid Unicode scalar ordering agrees with UTF-8 byte ordering, so string sorting
needs no allocated UTF-8 key copies. The retained test compares this result to an
independent encoded-key oracle, including BMP and non-BMP values. Sort is synchronous;
cancellation is checked before construction, between insertions, and after sort,
not during the host sort call.

The sizing review uses CPython 3.11.16 (`41388c9cb160d0886d5ca00d2e6c8782608a4549`)
and 3.14.7 (`823f0323ee6ec1402088b73bce1a38473cac36dc`) reference sources:
`Objects/listobject.c` (`list_resize`, `merge_getmem`) and `Objects/dictobject.c`
(combined-table insertion and resizing). These are read-only implementation
references, not new dependencies. As with the JSON admission profile, this sizing
review covers the pinned GIL-enabled builds; free-threaded delayed reclamation is
not qualified. Object headers, interpreter frames, reservation bookkeeping, and
third-party allocators are outside these payload counters.

Private builders are not mutable embedding APIs. Language operations produce new
collections; embedders must preserve heap pins and must not bypass builder methods
to mutate owned containers. Heap collection may clear unpinned containers. Surviving
container aliases can conservatively retain the reserved capacity until finalization.

Focused tests cover compiled/native and verified bytecode paths, persistent copies,
capacity rejection before covered allocation, retained failure tracebacks, cancellation
at each reached checkpoint, scalar aliases, key-layout replacement, and key ordering.
Full-suite and platform qualification remain required before the increment is merged.
Other producers, SQLite/TLS/cryptographic internals, and the broader issue #5
requirements remain open.
