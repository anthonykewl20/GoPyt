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
