# UTF-8 allocation reference and error probe

Read-only CPython reference commit 823f0323ee6ec1402088b73bce1a38473cac36dc,
Objects/unicodeobject.c: unicode_decode_utf8, _PyUnicodeWriter_PrepareInternal,
and unicode_decode_call_errorhandler_writer. The reference is not a dependency.
The decoder starts with a Unicode allocation; widening creates a new buffer and
copies characters before releasing the previous buffer. Error handling creates a
decode exception from the input pointer and length. A final-output-only reservation
therefore misses temporary overlap and exception-owned input.

The retained probe on both pinned Python versions checks three invalid UTF-8 inputs
(including 65537 bytes). Each UnicodeDecodeError owns an equal-content exact bytes
object distinct from the input bytes subclass. This confirms a real copy for these
cases, not an upper bound on all decoder allocation. Catch-and-convert must discard
the original exception before releasing temporary input-copy capacity.

The reviewed source commit is not proof of identical internals in every supported
runtime. Before choosing a general multiplier, inspect matching runtime sources or
use a conversion implementation whose allocation strategy is controlled. Python
object metadata, allocator overhead and runtime internals remain separate from
payload-capacity accounting. No conversion behavior is changed in this commit.

Resolved upstream release tags with git ls-remote: v3.14.7 peels to the existing
823f0323ee6ec1402088b73bce1a38473cac36dc reference; v3.11.16 peels to
41388c9cb160d0886d5ca00d2e6c8782608a4549. Leitir materialized the latter with
sampled verification. The source hashes are retained in upstream-sources.json.
These are matching upstream releases, not proof that standalone build patches
are absent from the installed binaries.

The 3.11 decoder begins with an ASCII allocation sized to input bytes and uses
a Unicode writer for the remaining input, widening as necessary. Its strict-error
path invokes the decode error handler and deallocates the writer on failure.
The 3.14 implementation has an additional code-point counting optimization.
Both therefore require decoder-temporary admission beyond the retained string.
Next inspect writer initialization, finish/shrink, and str-subclass construction
before finalizing the bound; the finite error-copy probe remains complementary.

The internal decode_utf8 helper now reserves 4*(input bytes+1) for its owned
Unicode payload and 8*(input bytes+1)+input bytes for decoder overlap and error
input. Writer initialization disables overallocation; widening and finish/shrink
can overlap buffers. str-subclass construction copies the Unicode payload and
terminator. Output capacity remains conservatively charged until alias destruction.
Three focused tests pass on each pinned runtime for roundtrip/hash/alias behavior,
rejection/invalid input cleanup and cancellation with a retained traceback.
The helper is not yet wired into language natives. Additional constructor/finalizer
fault coverage and integration qualification remain required.
