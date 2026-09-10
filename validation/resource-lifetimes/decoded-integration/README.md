# Decoded payload integration qualification

Frozen source 8c2d28f2b29239edaaeee51dda58c1e300a2853e; runtime
1524581b5d2203598387db87b34d43df36d47813f10e36ef2adff05891553e89.
Source hashes were verified unchanged after packaging and auxiliary checks.

Pinned Python 3.14.7 produces identical wheels at epoch 1788998400, SHA256
cfcae444c4bed3b2d6dbf0735db75cd195a9d225622af822b5c223d89eef6654,
58 runtime files. Installed smoke and old/new/rollback checks pass, as do 17 guard
tests, stdlib parity and the contract example.

Full language suites and final deadline inventory remain pending. Native UTF-8
conversion, byte/string concatenation, string slicing and model-body UTF-8 decoding
now admit covered payload copies. Parser objects, extracted model text, other
producers and native-library internals remain separate accounting gaps. This
increment does not complete issue #5 or establish a process RSS bound.

The first full Python 3.14 suite failed (1,056 tests; three failures and two
errors). Raw failure evidence is retained. The allocation audit passed no VM to
string concat; its test now supplies a cancellation context without changing the
expected allocation trap. Charged string/byte subclasses also exposed structural
equality using exact Python types: equal language scalar values could compare
unequal. Equality now handles string/byte values before the union-type distinction.
The 73-test audit/CLI/text selection passes on both runtimes after these fixes.
The runtime changed to 50589c241a1e079effc2a6dd29b60f97f0d31037c55daa3e306d6295bc45348e;
prior packaging and source metadata above refer to the earlier runtime and must
be requalified before publication. Full suites and deadline inventory are pending.
