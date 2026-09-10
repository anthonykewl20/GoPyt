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
