Operational metric evidence

Frozen source/runtime identity is in source.json. metric-set.json records the
declared closed metric set and an empty reading; it is not a measurement of any
workload.

Python 3.14.7 full language suite: 1,209 tests passed in 491.456s (full314.log).
Python 3.11.16 full language suite: 1,209 tests passed in 567.415s (full311.log),
with one test skipped because that run used an exported copy with no Git metadata.

Two wheel builds under SOURCE_DATE_EPOCH=1788998400 are byte-identical
(wheel SHA256 a3a511e00ad38aa5a431973e478d7f734fc7fccf4a5460796708e04e7da12242,
64 runtime files). Installed-runtime smoke, format 2 to format 3 upgrade and
rollback, 22 Guard tests, 22-module stdlib synchronization and all four
contract-demo outcomes passed.

The redaction tests establish rather than assume what telemetry may carry. Task
and trap names are read back from a compiled artifact in the test itself, so
"declared identifiers, not request data" is verified rather than asserted. The
one caller-supplied string, a `core.observe.note` tag, is driven with a secret
and shown to appear in no output at all. An earlier draft of that test used a
secret as a task name and failed, which is what established the distinction
between the two kinds of text and is why the test now checks the real property.

A test also asserts that every `observe.deny` kind emitted anywhere in the
runtime has a declared counter, so a denial added later without a counter fails
rather than going uncounted.

No source-mapped debugger or profiling service is provided, and diagnosis under
telemetry loss and overload is not qualified. Both remain open under #21.
