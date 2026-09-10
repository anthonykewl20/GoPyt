# Operations, incident response and recovery

This document is the operational half of the production gates in issue #26. It
publishes the supported platforms, the compatibility policy, the deployment and
monitoring runbook, the incident-response procedure, the recovery playbook and
the named release ownership.

Publishing a procedure is not evidence that it has been exercised at production
scale. The recovery objectives here are the targets frozen in
[production qualification targets](production-qualification-targets.md); they
are measured in issue #25, which is open. No release may be described as meeting
them before that measurement exists and issue #26's independent review is
complete.

## Release ownership

| Role | Holder | Responsibility |
|---|---|---|
| Release owner | The repository owner, GitHub `anthonykewl20` | Approves a release, holds the signing identity, decides withdrawal and re-issue |
| Security contact | The repository owner, through GitHub private vulnerability reporting | Receives reports, triages severity, coordinates disclosure |
| Deployment owner | The operator running the service | Gateway, TLS, isolation, secret custody, backups, monitoring, capacity |

This project has a single maintainer. There is no separate on-call rotation and
no availability commitment attached to these roles. An operator who needs one
must provide it themselves; the deployment responsibilities above are theirs by
design, not by omission.

## Supported platforms and compatibility

| Item | Supported |
|---|---|
| Production platform | Linux x86-64, glibc, container runtime |
| Interpreters | CPython 3.11.16 and 3.14.7, both exercised in CI |
| Development-only platform | macOS arm64 (`macos-15`), language behaviour only |
| Unsupported by decision | Windows, 32-bit targets, non-glibc Linux, multi-node clusters, GPU or accelerator execution, hostile code in the host process |

Bytecode artifacts are bound to an exact toolchain fingerprint: a `format 3`
artifact refuses to load on a runtime whose source identity differs, and the
upgrade and rollback path is qualified in both directions on every release. The
[toolchain compatibility amendment](toolchain-compatibility-amendment-2026-09-09.md)
is normative for that binding. Package and dependency compatibility rules are in
the [lockfile](lockfile.md) document; release artifact identity, verification and
withdrawal are in [release provenance](release-provenance.md).

## Deployment runbook

1. Install the signed wheel and verify its attestation against the approved
   source and workflow identity before trusting it. A wheel's attestation does
   not authenticate an adjacent inventory file; verify that separately.
2. Provision secrets per the [key and credential lifecycle](key-lifecycle.md):
   private files outside the package, owned by the service account, mode 0600,
   no symlink or hard-link path components.
3. Set the strict profile: `GOPYT_SECURITY_PROFILE=strict`,
   `GOPYT_STORE_KEY_FILE`, `GOPYT_STORE_ID`, `GOPYT_HTTP_TOKEN_FILE` and a
   numerical loopback `GOPYT_HTTP_ADDR`.
4. Set the transport boundary per the
   [transport and gateway amendment](transport-gateway-amendment-2026-09-10.md):
   `GOPYT_HTTP_GATEWAY_ADDR` for the trusted terminator,
   `GOPYT_HTTP_FORWARDED_HEADER` and `GOPYT_HTTP_FORWARDED_REQUIRED` for client
   identity, `GOPYT_HTTP_RATE` for identity-aware admission.
5. Set `GOPYT_GUARD_ISOLATION=required` if the deployment relies on the
   [OS isolation profile](os-isolation-amendment-2026-09-10.md) for Guard
   acceptance, so evaluation refuses rather than running unisolated.
6. Enforce the memory ceiling in the container, not in the application: the
   runtime's counters are admission accounting, not aggregate resident memory.
7. Verify the service answers on loopback only, and that the gateway is the sole
   route to it.

## Monitoring

Emit and alert on, at minimum. The counter names below come from the closed
metric set in [observability](observability.md):

| Signal | Why | Alert when |
|---|---|---|
| HTTP status mix, especially 401/403/429/503/504 | Admission, identity and overload behaviour | 503 or 429 sustained above baseline, or any unexplained 403 |
| Request latency percentiles | The frozen p50/p99/p99.9 targets | p99 above 250 ms over a sustained window |
| Container resident memory | The 2 GiB hard cap is external | Above the 1.5 GiB steady-state target |
| Resource-budget denials and allocation traps | The `alloc:` counters name which budget refused work | Any sustained rate |
| Authorization denials | The `deny:` family counts every surface's refusals together | Any unexplained rate, especially `deny:gateway` |
| Queue pressure | `queue:connection_rejected`, `queue:worker_refused`, `queue:request_expired` | Any sustained rate |
| Retry conflicts | `conflict:compare_exchange`, `conflict:writer_fence`, `conflict:snapshot_generation` | A rate that does not fall after a deploy |
| Contract failures | `contract:precondition`, `contract:postcondition` | Any occurrence in production |
| Storage lock contention and snapshot publication failures | The single-writer bottleneck | Any failure |
| Guard receipts with `isolation.installed` false | A profile that should be installed is not | Any occurrence under a `required` policy |
| Egress denials with reason `address` | An allowlisted origin resolving outside its permitted addresses | Any occurrence |
| Dependency and advisory alerts | Supply chain | Any open alert |

## Incident response

**Severity.** S1: confirmed data loss, data disclosure, or authentication or
authorization bypass. S2: integrity or availability impact without disclosure,
including a failed restore drill. S3: degraded behaviour within documented
bounds. S4: cosmetic or documentation-only.

1. **Report.** Security reports arrive through GitHub private vulnerability
   reporting. Do not publish credentials or exploit detail in an ordinary issue.
2. **Triage.** The security contact assigns a severity within one business day
   and records the affected versions and source identities.
3. **Contain.** For S1 and S2, stop the affected path first: withdraw the
   release per the provenance policy, rotate the affected credential per the key
   lifecycle, or take the deployment out of the gateway's rotation. Containment
   precedes root cause.
4. **Preserve.** Retain the failing evidence — logs, receipts, snapshots and
   source identities — before repairing. A repaired system without retained
   failure evidence cannot be reviewed afterwards.
5. **Remediate.** Fix with a regression test that reproduces the failure, and
   qualify it on both supported interpreters and both CI platforms.
6. **Disclose.** Record severity, affected versions, the fix, residual risk and
   any scenario that remains unsupported. Publish the revocation if a release
   was withdrawn.
7. **Review.** Record what the existing gates did not catch and what was added.

## Recovery

The frozen objectives are **RPO 0 committed transactions lost** and **RTO 900
seconds** from node loss to serving. Neither has been measured; #25 owns that.

**Backups.** Back up the storage snapshot, its authority state and the key
material separately, on the operator's own schedule. Losing the key loses the
data: encryption fails closed, and there is no recovery path around it.

**Restore drill.** Practise the whole path, not the parts:

1. Provision a clean host and install the same verified wheel.
2. Restore the key material first and confirm the store identity matches.
3. Restore the snapshot and the rollback authority together. Restoring a
   snapshot without advancing its authority is what replay protection exists to
   catch, and it will be refused.
4. Start the service and confirm it answers behind the gateway.
5. Reconcile against an independent oracle, not against the service's own
   report.
6. Record the elapsed time and compare it to the 900-second objective. A drill
   that misses the objective is reported as a miss; the objective is changed
   only by a revision in the frozen targets.

**What recovery does not cover.** A restored older valid snapshot is detected as
replay only when the operator protects a separate rollback authority from the
same loss. Beyond-RAM retained state is not supported by the current backend at
all — the frozen 10 GiB target is what issue #8 must deliver, and until it does,
recovery is qualified only for the bounded snapshot the backend actually holds.
