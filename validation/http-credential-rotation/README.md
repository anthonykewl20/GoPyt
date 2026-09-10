# Live HTTP credential rotation qualification

The [design](../../docs/http-credential-rotation-design.md) was committed as
`9b40817` before implementation. The [load protocol](protocol.json) was committed
as `2d5e068` before workload development and observations. Runtime/workload commit
`fcd2ca2` fixes the measured sources; runtime fingerprint is
`59c09481e714507d9e17ec27f89030303b24147ec7d631f19fd409c88c427079`.
The [normative amendment](../../docs/http-credential-rotation-amendment-2026-09-10.md)
defines admission, reload failures and in-flight request semantics.

The same before/after probe changes the post-replacement old/new response pair
from `[200, 401]` to `[401, 200]`, without restarting the listener. Its source hash
is identical in both results; no credential bytes are recorded.

## Frozen concurrent workload

Every round holds one request already admitted using the old token across atomic
file replacement. It then sends three requests with the new token and four with
the revoked token concurrently. Those requests must return 200 and 401 respectively;
the earlier admitted request must complete with 200. The exact response bodies and
four handler admissions are checked. Each run uses eight clients, eight workers,
a 32-entry queue, three warmups, at least 30 measured rounds and at least 60 seconds.
The maximum complete round budget is ten seconds, fixed before observations.

| Python | Measured rounds | Requests | Elapsed seconds | p99 round ms | Maximum round ms |
|---|---:|---:|---:|---:|---:|
| 3.14.7 | 6,186 | 49,488 | 60.111 | 41.047 | 264.400 |
| 3.11.16 | 8,403 | 67,224 | 60.110 | 12.553 | 49.914 |

Both sequential Linux campaigns passed with unchanged source hashes and budgets.
Elapsed time includes final listener cleanup; round latency includes coordination
and credential-file durability work. These are observations of this workload,
not production throughput/latency SLOs or a general Python-version comparison.

`measured314/` and `measured311/` retain pre-run Python/runtime/source identities,
raw per-request outcomes, round latency, execution logs and reports. The driver
checks source hashes again at completion and checks that generated token values
never appear in execution logs. Response bodies are compared exactly and contain
no credentials. The offline `verify.py` imports no GoPyt implementation and
rechecks literal authorization outcomes and budgets; both datasets passed it.
A deliberately corrupted new-token outcome is required to fail the oracle.
The environment ledger explicitly records that its platform description was
captured afterward on the same execution host.

## Regression and build evidence

`focused314.log` and `focused311.log` contain 65 passing tests each, covering the
four new live-credential tests and existing server, security, connection, identity,
response-budget and retention behavior. New cases include keep-alive reuse,
missing/malformed/nonprivate/symlink credential files, recovery, pinned startup
mode/path and admitted-request completion after rotation.

`guard.log`, `stdlib.log`, `build-report.json`, `repro.log`, `wheel.log` and
`upgrade.log` retain Guard calibration, stdlib parity, 47-module reproducible wheels,
installed entry points, and compiler/runtime upgrade then rollback. All 50
packaging inputs and both measured source inventories were rechecked unchanged.
`full314.log` records all 902 full tests passing in 344.758 seconds;
`contracts.log` records the runnable contracts example passing.

## Trials and limits

Two-round smoke runs passed on each Python and are retained separately. The first
expanded regression invocation named a nonexistent `test_http_request_budget`
module; `initial-focused314.log` and `initial-focused311.log` retain those loader
errors. The corrected 65-test invocations use the actual suite names. No runtime
code or acceptance budget was changed to address that command error. Neither
measured campaign failed.

Reads still use synchronous filesystem calls; host blocking can exceed requested
budgets. Token-file snapshots already opened by admitted requests can remain valid
for those requests. Rotation does not revoke their completed effects. Atomic
replacement, private paths and secret distribution remain operator obligations.
File mode checks do not establish hardware custody or host isolation. Issue #11's
plaintext migration, encrypted-format transitions, stale storage writers, lost-key
handling and backup/custody procedures remain separate from this increment.

The fresh `deadline-inventory.json` passed on both pinned Python versions, covering 73 fixed natives, 12 policy groups and three generated conversion forms. It records the per-request credential-read boundary and pins the reviewed implementation/evidence commit; older inventories retain their original identities.
