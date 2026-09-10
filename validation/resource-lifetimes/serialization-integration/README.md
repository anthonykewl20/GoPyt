# Serialization admission integration qualification

Frozen source: ce01ff334eadf21839bfb85bffac986426703b8e.
Runtime: 2aeb713185c91d08cbcd041bdbfedb81a94ec10384589a1e7d8b0c9087a7166f.
The source manifest covers runtime/test Python files and nine lock files.

Pinned Python 3.14.7 packaging checks pass. Two wheel builds at epoch 1788998400
are bitwise identical, SHA256
8aa8725cec6fdf35fe8024e10c941bc6b39628de28126fa810554fc7afb21d25,
with 57 runtime files. Installed smoke checks pass outside the checkout, as do
old/new/rollback checks against the retained baseline wheel. The 17 guard tests,
22-module stdlib parity check and contract example pass.

The full Python 3.14.7 language suite passes 1,035 tests in 361.060 seconds.
Frozen source hashes were verified unchanged. Python 3.11.16 full qualification
is pending. This
increment covers serialized image payload capacity; it does not bound all SQLite
or cryptographic allocations, restore backup producers, decoded values or explicit
copies. Issue #5 remains open.
