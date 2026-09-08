# Implementation closure and second review — 2026-09-05

> Release numbering correction: the current initial release is **0.1.000**;
> see the [current release policy](../README.md#implementation-status).
> This report and its archived evidence record the earlier **0.2.0** development
> label under which these checks ran. Bytecode format remains **version 2**.

## Assessment

The six open areas in the [first audit](implementation-audit-2026-09-05.md)
now have implemented fixes, explicit contracts, and regression coverage. The
runtime remains a Python-hosted compiler/bytecode VM; its language heap now has
its own tracing collector. The release is **gopyt-0.2.0**, with **bytecode version
2**. Application signatures and effects were not widened to make tests pass.

This is measured implementation validation, not an exhaustive proof over all
programs, hostile operating systems, network providers, or thread schedules.
Platform and empirical limits below remain explicit.

## Closure of the six findings

| Finding | Final implementation | Verification |
|---|---|---|
| **U01: tracing GC** | `heap.py` owns a nonmoving mark-and-sweep registry. It traces frame stacks/locals, constants, native arguments/results, scheduler captures/results, and explicit embedding pins. Sweeping breaks container cycles. Interpreter safepoints use a mutator lock; blocking natives/parallel joins release it with roots pinned. | Cycles reclaimed with Python cycle GC disabled; transitive roots retained; collector runs concurrently with parallel calls; native-only allocation pressure and 6,000 repeated calls trigger collection; HTTP and auth load probes remain bounded. |
| **U02: bounded hardening state** | Fixed 8,192-bit Bloom filter, Count-Min/Welford/CUSUM, saturating counters, capped compact trace rows, and atomic `build/traces` snapshots. Automatic nominal/enum task outcomes use a documented failure-name set. Limiter keys are SHA-256 digests; capacity is 4,096; exhausted entries cannot be evicted to obtain fresh tokens. | 100,000 unique auth requests; fixed observe cells and limiter capacity; parameter-change bypass refused; 10,000 pressure attempts do not scan the full table before refill; repeated dumps do not append; tags have a byte bound. |
| **U03: evolution** | Durable undo journal, fsync, serialized tool access, rollback before source loading after interruption, committed-journal cleanup, and refusal on observed external conflicts. Candidate digest is frozen and rechecked before apply. Four persistent strategy weights and tag-only contract-trap CUSUM replay gate selection. Evolve policy is serialized in the artifact. | Real subprocess termination after each source/lock write; recovery and idempotence; interrupted cleanup; ordinary write failure; concurrent reader blocking; candidate tampering; dependency staging; timeout; persistent weights; independent artifact load. |
| **U04: loop locals** | Version-2 `RESET_LOCAL` explicitly ends a non-parameter slot activation. Loop lowering emits resets for reused locals/temporaries. `STORE_LOCAL` always traps on an initialized slot; broad back-edge exemptions are removed. Verifier resets invalidate stale alias refinements. | Existing loop/nested-loop suites; resets of parameters and loads after reset rejected; a never-taken back edge cannot exempt a double store. |
| **U05: authoring/spec consistency** | Explicit contextual-keyword and diagnostic-order amendments. Formatter preserves evaluation order and sorts existing imports. Missing implementation repairs preserve completed bodies and include custom trait members. Structural identity/constructor proofs and HTTP `serve` are elaborated. Json/eligible FromStr are compiler-owned derivations; overrides are rejected. Missing required tests get canonical unresolved stubs. | Determined identity/constructor execution; custom trait stub reaches E030; missing-test repair reaches E030; completed body preserved; arbitrary serve body rejected; real HTTP FromStr without a handwritten provide; existing formatter/diagnostic determinism suites. |
| **U06: release validation** | Versioned package metadata, installable console entry point, Linux/macOS CI matrix, a live interactive agent repair trial, TLS/proxy checks, richer artifact mutations, and sustained local VM/HTTP probes. | Final interpreter matrix in the evidence manifest; clean-wheel installation; 20-module stdlib sync; P0 calibration; one P2 trial reaches check zero in four cycles; real TLS and 2,000 HTTP requests. Remote CI/macOS and external provider certification are not claimed. |

The [normative amendment](runtime-amendment-2026-09-05.md) identifies the exact
contract changes. The old audit remains historical rather than being rewritten
to imply these mechanisms existed during the first review.

## Defects found during the second review

These were found while testing the closure implementation, not merely carried
over from the first report.

1. **Calls could reach unvalidated later-function metadata.** Mutation of a rich
   auth artifact exposed an `IndexError` in the type verifier. The loader now
   validates every callable's metadata before following any call edges.
2. **Equivalent union members could use different type-expression indices.**
   Duplicate semantic members are now rejected even when their indices differ.
3. **Ambient proxies could change the transport path.** HTTP and model natives
   now explicitly disable environment-derived proxy handlers. A real proxy
   endpoint receives no request during the regression test.
4. **HTTP-only FromStr derivations were incomplete.** The checker now installs
   their signatures, the loader verifies their exact single-field shape, and
   the native constructs the record. The previous handwritten test provide was
   removed, so it cannot mask a missing compiler derivation.
5. **Observe calls counted their own measurement work.** Observe natives are
   excluded from automatic outcome counting; manual notes remain one event.
6. **GC transfer boundaries needed explicit roots.** Return values, parallel
   results, and HTTP parameter accumulation are rooted across collection and
   native/interpreter handoffs. Worker/request cleanup releases transfer roots.
7. **Source reads and candidate copying retained path-based open races.**
   Descriptor traversal now rejects symlinks in every root/path component.
   Parsed source bytes are cross-checked against the digest pass. Candidate
   copying uses descriptor-relative regular-file reads.
8. **A prepared candidate could change before apply.** Its digest is now bound
   into the preparation result; authority and source consistency are rechecked.
9. **A full limiter table made every new key scan 4,096 buckets.** An
   earliest-expiry lower bound avoids these scans while no entry can expire.
10. **The prescribed serve body was generated but not enforced.** The checker
    now requires exactly `return net.http.serve()` for an HTTP module's serve
    implementation, and rejects additional arbitrary body statements.
11. **New artifact policy bounds needed a source-side upper limit.** Evolve
    bounds outside `1..2147483647` now produce E117 before encoding.
12. **Persistent weight input needed a read bound.** The optional four-weight
    state file is read with a 4 KiB cap before parsing.

## How the critical boundaries work

The collector owns reachability; Python supplies storage cells. A cell stays
in the registry until the collector traces the explicit roots and sweeps it.
The cycle test disables host cycle collection and checks that the cell becomes
unreachable only after the language collector severs its edges. Native foreign
allocations are adopted on return, and arguments stay pinned during blocking
calls. Embedders retaining a result across subsequent calls must use
`vm.heap.pin(value)` as described in the amendment.

Evolution provides transaction isolation for cooperating GoPyT tools, not a
single filesystem instruction that replaces an arbitrary tree:

| Journal state at next load | Action |
|---|---|
| No journal | Load normally under the package lock. |
| Prepared | Check recorded byte images, restore the old source and lock, then remove the journal. |
| Committed | Verify the committed bytes and remove the journal without rollback. |
| Unexpected external bytes | Refuse recovery/load; preserve the conflicting edit. |

All source writes follow the durable prepared journal; the checked lock is
installed last, and the commit marker is durable before cleanup. Independent
editors can ignore advisory locks. Detected conflicts are refused, but an editor
racing precisely between a comparison and replacement is outside that isolation
guarantee. Such writers must coordinate through the same lock.

Limiter overflow fails closed. Only a fully replenished bucket can disappear;
changing a depleted key's token/refill parameters does not grant new tokens.
The fixed Bloom filter is telemetry, not authorization: false positives cannot
admit a denied request or reset a bucket.

Evolution fitness has a deliberately narrow meaning. Replaying the same stored
contract-trap indicators normally gives identical CUSUM scores for checked
candidates. Persistent weights penalize failed gates; they do not establish
that a candidate reduced false denials, improved a business rule, or learned
from unseen payloads. No application network/database effects are replayed.

## Validation evidence

Final verification is recorded in
[the evidence manifest](audit-evidence/2026-09-05-closure/summary.json).

| Check | Result |
|---|---|
| Full unittest suite, Python 3.11 / 3.12 / 3.13 / 3.14 on Linux | **434 tests pass on each interpreter; zero failures/skips** |
| New closure tests | 33 methods, independently probing the boundaries above |
| Conformance fixtures | All 54 fixture packages covered by the full suite |
| Artifact mutation probes | 2,000 minimal-artifact mutations plus 2,000 rich auth-artifact mutations |
| Arithmetic reference probe | 250 generated expressions checked against an independent integer evaluator |
| P0 | Six prototype tests; 20 calibration trials; 0/20 control alarms; 20/20 planted shifts detected, 38–120 events after the shift |
| P2 | One interactive Codex root-agent trial, bounded at 10 cycles; check clean at cycle 4; fmt/test/check exit 0; spec unchanged |
| TLS and model wire protocol | Untrusted certificate refused before a POST; trusted local test certificate accepted; exact prompt JSON and response shape checked |
| HTTP load | 2,000 real requests, 16 clients, correct typed JSON responses; details in `http-load.json` |
| Auth/state load | 100,000 distinct login keys; table capped at 4,096; observe cells fixed at 2,424; 46 live heap cells at each sampled post-collection point |
| Packaging | Clean virtual-environment wheel install; installed executable passes fmt/check/test/run on a copied auth package |
| Catalog/build | 20 stdlib modules synchronized; compileall and local documentation links checked |

The P0 prototype's 1,400 cells count only its original sketches/reservoir. The
runtime's 2,424-cell accounting also includes the 1,024-byte Bloom buffer.
These logical counts are not measurements of total process RSS or Python object
header overhead.

Reproduce the core checks from the workspace root:

```sh
python3 -m unittest discover -s gopyt -t . -p 'test_*.py'
python3 -m unittest gopyt.test_closure
python3 prototypes/observe/test_sketches.py
python3 prototypes/observe/calibrate.py
python3 tools/check_stdlib_sync.py
python3 -m compileall -q gopyt tools prototypes
python3 tools/runtime_probe.py --output /tmp/gopyt-runtime-evidence
python3 -m pip wheel --no-deps --no-build-isolation . -w /tmp/gopyt-wheel
```

The interactive P2 transcript and final package are preserved in the evidence
directory. It is a single successful agent trial with access to this task's
context, not a blind model benchmark or a measured general repair success rate.

## Release boundaries

- **Locally verified:** Linux; supported Python versions in the matrix; local
  TCP/TLS; process termination recovery; targeted concurrent filesystem probes;
  bounded 100,000-call VM and 2,000-request HTTP experiments.
- **Configured but not executed here:** the GitHub Actions matrix, including
  macOS. This workspace has no Git metadata or remote to run that workflow.
- **Not supported in this release:** native Windows filesystem/locking behavior.
- **Not established by these tests:** external provider credentials/availability,
  multi-day uptime, universal filesystem adversary resistance, or correctness
  of arbitrary business logic. The release remains alpha.

Those are limits on the evidence and supported environment; they are not hidden
claims that a green test suite proves universal safety.

## Handoff

- Toolchain: **gopyt-0.2.0**. Bytecode: **version 2**; version 1 must be rebuilt.
- Compiler repository package digest: **none**; the repository has no root
  `gopyt.toml`. Compiler and file fingerprints are in the evidence manifest.
- Application public signatures/effects/contracts changed: **none**.
- Application `spec/` and `impl/` files changed: **none**. Probes use temporary
  packages. Fixture/example lock toolchain lines were migrated to 0.2.0.
- Language/runtime contract changes: explicit RESET_LOCAL/artifact-policy,
  heap/root, limiter/trace, transaction, authoring and platform amendments.
- Implementation/test/document changes: exact file hashes in the manifest.
- Failed final tests: **none**. Skipped final tests: **none**.
- Original U01–U05 implementation gaps: **addressed**.
- U06: local release work and experiments **addressed**; unexecuted platform and
  provider claims remain explicitly outside the evidence above.
- Runnable auth `open`/`unresolved` ids: **none**.
- Commit/PR: **none**; no Git checkout or configured remote was supplied.
