# Private-file key lifecycle acceptance

The [operator runbook](../../docs/key-lifecycle.md) defines provisioning, named
custody responsibilities, storage and HTTP rotation/revocation, reference retention,
retirement gates and lost-key decisions. Its source was committed before the drills.
This does not certify external custody infrastructure or a production deployment.

## Requirement mapping for issue #11

| Requirement | Implementation and evidence |
| --- | --- |
| Provisioning, custody, rotation, revocation and retention procedures | Runbook; `provision.py` executes its exact Python snippet on both pinned interpreters, verifies private output and refuses overwrite; `drill.py` exercises escrow recovery and retirement prerequisites. |
| Explicit recoverable plaintext migration and supported transitions | [Migration qualification](../plaintext-storage-migration/README.md), normative migration and writer-fence amendments; plaintext-to-SIV1, SIV1 key rotation and authority v1-to-v2 are supported. Unknown future cipher formats reject. |
| Stale writers, interrupted rotation and fresh-process restoration | [Writer-fence qualification](../storage-writer-fence/README.md), actual waiting writer and publication crashes; this drill restores an old backup under the current key and verifies fresh current-key-only reads. |
| Wrong/missing/lost keys, corrupt inputs, incompatible settings and live credential rotation without secret logs | Full migration/rollback/security regression at the unchanged runtime; both lifecycle drills reject missing key files after a cache hit and in fresh processes, restore escrowed material, independently decrypt/compare restored rows; both frozen HTTP campaigns and offline verifiers. |
| Reviewed decisions, references, tests and failure evidence | Frozen migration/fence designs, linked normative amendments, source identities and retained regression/probe trials; the runbook's exact snippet and lifecycle drill are retained here. |
| Frozen data/source identities, independent oracles and actual limits | Input inventories precede each run; independent AESGCMSIV/SQLite checks and offline HTTP outcome verification; measured coordination latency is retained without a production SLO claim. |
| User-facing documentation and architecture tracker | Runbook linked from SECURITY, docs index, rotation procedure and architecture programme; this matrix preserves the supported profile and external-custody limits. |

## Qualification scope

The runtime is unchanged from migration qualification:
`60646df704fc17b97d203189a4dfbafd3ee62bfc8da501ace90e2d9cdfdd2045`.
Its 919 full tests, 90 focused tests per pinned interpreter, Guard, stdlib, contracts,
reproducible/installed wheel, upgrade/rollback and current deadline inventory evidence
remain under `validation/plaintext-storage-migration/`. This increment adds
procedures and executable acceptance evidence, not runtime or dependency changes.

`drill311/` and `drill314/` retain source/interpreter identities, fresh-process events
and results. The drills use temporary same-host escrow locations and prove runtime
recovery behavior; they do not prove an external vault's availability or separation.
The missing-key scenario removes the configured file; it is not a cryptographic
proof of irrecoverability or a claim that no other host copy can exist. The procedure
explicitly forbids treating newly generated keys or empty stores as restoration.
Secret raw bytes, hexadecimal and base64 representations are checked against the
retained drill artifacts before temporary keys are discarded.

`provision311.json` and `provision314.json` pin the exact runbook and snippet-check
source. Refusal to overwrite is an expected negative control; neither secret value
nor traceback is printed to the retained result. No unexpected failed lifecycle
trial occurred. Historical unsuccessful migration setup trials remain linked in
that feature's evidence and are not reclassified as successes.

HTTP campaigns reuse the previously frozen protocol in
`validation/http-credential-rotation/protocol.json` without changing budgets or
expected outcomes. They run sequentially, use the current runtime, retain every
round, and pass the independent offline `verify.py`. The protocol includes an old
request admitted before replacement, three newly authorized requests and four
revoked requests per round, plus outcome-negative and no-secret-log checks. These
are controlled Linux trials; latency includes coordination. Process crashes are
not power-loss tests; finite acceptance is not universal security certification.

## Current-runtime HTTP results

| Python | Rotations | Requests | Seconds | Maximum round ms | p99 round ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| 311 | 7005 | 56040 | 60.110 | 176.239 | 68.150 |
| 314 | 7954 | 63632 | 60.146 | 210.281 | 44.127 |

All captured source hashes were rechecked unchanged after both campaigns.
