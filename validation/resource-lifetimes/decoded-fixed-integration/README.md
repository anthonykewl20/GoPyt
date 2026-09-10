# Corrected decoded payload qualification

Frozen source f4305381490cfc549118a238f545b9d7a9f64813; runtime
50589c241a1e079effc2a6dd29b60f97f0d31037c55daa3e306d6295bc45348e.
Source hashes were verified unchanged after packaging and auxiliary checks.

Pinned Python 3.14.7 builds identical wheels at epoch 1788998400, SHA256
a5271a05b38d86cc275b3a580d7f4dcfc1cf5196798f7a7c28885d5a8617f34e,
58 runtime files. Installed smoke, old/new/rollback, 17 guard tests, stdlib parity
and contract checks pass. The full Python 3.14.7 suite passes 1,056 tests in 362.225 seconds. Frozen
source hashes were verified unchanged. Python 3.11.16 also passes 1,056 tests in 411.202 seconds. Source hashes
remained unchanged through both full runs.

Earlier full-suite failures and the scalar equality correction are retained in
../decoded-integration. Parser objects, extracted model text, other producers and
native-library internals remain gaps. This increment does not complete issue #5.
