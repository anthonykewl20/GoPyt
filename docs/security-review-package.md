# Independent security review: scope package

Issue #26 requires an independent review of the compiler and loader, the
natives, the capability model, the storage and cryptographic boundaries, Guard
isolation, and deployment. This document is the package a reviewer needs. It is
**not** a review, and nothing in this repository may be presented as one: a
self-assessment by the people who wrote the code does not satisfy independence,
and neither does an assistant's inspection of it.

## What to review

| Surface | Entry points | Boundary it claims |
|---|---|---|
| Compiler and loader | `gopyt/check.py`, `gopyt/parser.py`, `gopyt/gobyte.py`, `gopyt/toolchain.py` | Rejects malformed, contract-weakening and toolchain-mismatched input; never executes application source at compile time; an artifact loads only on its exact runtime source identity |
| VM and natives | `gopyt/vm.py`, `gopyt/natives.py`, `gopyt/heap.py`, `gopyt/ops.py` | Closed native catalogue, effect checking, allocation ceiling, resource budgets, deadlines, cancellation, tracing heap ownership |
| Capability model | `gopyt/resource_authority.py`, `gopyt/identity.py`, `gopyt/capabilities.py` | Host-issued attenuated handles, revocation, operator-provisioned subjects, tenant-bound sessions, per-request authorization |
| Storage and crypto | `gopyt/storage.py`, `gopyt/security_config.py`, `gopyt/rollback.py` | AES-256-GCM-SIV per snapshot bound to a deployment identity, writer-key fencing, atomic replacement with fsync, opt-in rollback authority, explicit plaintext migration |
| Transport | `gopyt/server.py`, `gopyt/gateway.py`, `gopyt/net_policy.py`, `gopyt/netio.py` | Trusted-gateway peer and forwarded identity, this server's own header bounds, identity-aware admission, resolved-address egress policy, redirect and proxy refusal |
| Guard isolation | `gopyt/guard.py`, `gopyt/isolation.py`, `gopyt/isolated_exec.py` | Bundle and engine pinning, transitive contract-helper closure, Linux namespace profile with a read-only private root and explicit rlimits, fail-closed policy |
| Supply chain | `.github/workflows/`, `requirements/`, `tools/release_components.py`, `tools/adaptation_inventory.py` | Hash-pinned build inputs, immutable action commits, reproducible wheels, signed provenance, complete component and adaptation inventories |
| Deployment | `SECURITY.md`, `docs/operations.md` | What the operator owns: TLS, network exposure, OS isolation, secret custody, backups, monitoring, incident response |

## What is already frozen for you

- [Production qualification targets](production-qualification-targets.md): the
  deployment envelope, the unified attacker model across all ten surfaces, the
  correctness invariants and budgets, the control allocation, and the frozen
  dataset, oracle and threshold identities. Read this first; it defines what is
  in scope and what is deliberately out.
- [Release component inventory](component-inventory.md) and
  `validation/component-review/`: every pinned distribution, interpreter
  archive, action commit, vendored and native component, with licenses, plus the
  repository-wide adaptation inventory.
- The normative amendments listed in [the document index](README.md), each
  stating its own limits.

## How to reproduce what is claimed

```sh
python -m pip install --require-hashes -r requirements/build.txt -r requirements/security.txt
python -m pip install --no-deps --no-build-isolation -e .
python -u tools/run_language_tests.py discover -s gopyt -t . -v   # full language suite
SOURCE_DATE_EPOCH=1788998400 python tools/reproducible_wheel.py --source . --output /tmp/wheels --epoch 1788998400
python tools/wheel_smoke.py /tmp/wheels/0/gopyt-0.1.0-py3-none-any.whl
python tools/release_components.py        # component inventory drift check
python tools/adaptation_inventory.py      # external reference classification
python tools/check_workload_freeze.py     # frozen targets integrity
python tools/check_deadline_inventory.py validation/deadline-boundaries/inventory.json
```

Retained evidence, including failures, lives under `validation/`. Every
qualification directory carries a `source.json` freezing the exact file hashes
its results describe. Where a run failed and was corrected, the failing run is
retained beside the passing one and says so.

## Known gaps, so you do not have to rediscover them

These are recorded in the frozen targets and their issues, and a reviewer should
treat them as stated limitations rather than findings:

- **#8** The backend bounds one snapshot at 64 MiB and rewrites it whole. It
  cannot hold the frozen 10 GiB retained state.
- **#5** SQLite, TLS and cryptographic internal allocations are outside the
  resource budget. Counters are admission accounting, not aggregate resident
  memory.
- **#6** The isolation profile is qualified by finite hostile fixtures on one
  Linux kernel. macOS has no supported profile, no system-call filter is
  installed, and tenant separation between concurrent evaluations is unqualified.
- **#25** No measurement exists against any frozen latency, throughput,
  availability or recovery objective.
- **#9, #14, #15, #16, #18, #19, #20, #21** Typed schemas and migrations,
  stateful Guard acceptance, cross-package Guard closure, a native execution
  path, streaming and columnar access, shared-memory IPC, editor tooling, and
  production observability are unimplemented or partial.

## What a completed review must produce

1. Findings with severity, each reproducible against a named source identity.
2. An explicit statement of what was and was not examined, and with what effort.
3. Residual risks and threat scenarios that remain unsupported after remediation.
4. A judgement on whether the boundaries above hold as described, separate from
   whether the tests pass.

Remediation is then tracked against #26's second criterion with regression
evidence, and the review deliverable is linked from that issue. Until such a
deliverable exists, this project's security posture is **unreviewed**, and
`SECURITY.md` says so.
