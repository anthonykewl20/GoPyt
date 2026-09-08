# Language baseline: 2026-09-09

This language-only checkout implements the first increment of the approved backend, correctness, editor, security, performance and release priorities. It excludes the separate model-training workspace. All results below concern local GoPyT tests, not a newly measured VulcanBench model score.

## Correctness and security

The initial complete language suite passed **644 tests in 200.321 seconds** on the local Linux/Python 3.14 environment. The final provider-error handling adjustment then passed its seven security integration tests. Logs are in [validation/release](../validation/release/). An earlier run failed the nested diagnostic-coverage audit while new LSP test fixtures were being corrected; its failure is retained, followed by the clean final run.

New generated-program validation compares 48 arithmetic/branch programs at 11 boundary inputs each, including signed 64-bit overflow. Inventory validation compares 400 seeded state transitions to an independent reference model and passes eight real HTTP lifecycle/concurrency/restart checks. These are constructed workloads, not an exhaustive program proof or customer production dataset.

A reproduced hard-link flaw allowed an application write to truncate a file outside its package. General file natives now reject multi-link regular files before access/truncation, and reserved source/runtime paths are protected. The before-repair reproduction is retained as `hardlink-before.json`; regression tests keep the outside sentinel unchanged.

Authenticated storage tests exercise encrypted restart, conditional updates, ciphertext tampering, wrong/missing keys, changed store identities, cached reads and refusal of implicit plaintext migration. A real HTTP subprocess test rejects missing, incorrect and duplicate credentials, verifies no unauthorized state mutation, then writes an authorized reservation and restarts from encrypted state. The strict profile and limits are documented in [SECURITY.md](../SECURITY.md) and the [normative amendment](security-profile-amendment-2026-09-09.md).

The security extra is an explicit project dependency choice, independent of Leitir source discovery. A pip-audit scan reported no known advisories for the three pinned security-extra packages at execution time; this is not a code audit or a guarantee against undisclosed vulnerabilities.

## Backend performance

Three alternating before/after runs measured median GET latency with 40 requests per run:

| Variant | Median of run medians |
| --- | ---: |
| Unconditional snapshot write | 6.813 ms |
| Skip unchanged snapshot write | 4.516 ms |

This is a **1.51x latency ratio** on this local workload. An 80-request/eight-worker run measured **44.52 ms p95**. Raw timings and final states are in [inventory.json](../validation/release/inventory.json). These development-profile measurements do not include encryption overhead, sustained saturation, multi-machine load or power-loss testing. The machine was shared with other work, so timings are indicative rather than isolated hardware certification.

## Mapped-data result

Leitir global searches for `MAP_SHARED` and `F_SEAL_SHRINK` produced partial, explicitly bounded GitHub results. A verified commit of `a-darwish/memfd-examples` supplied a licensed seal-before-sharing reference. The local prototype adapts that pattern, adds growth sealing and format/index validation, and introduces no donor dependency.

Twelve subprocess runs over a synthetic 8 MiB i64 column matched independent sequential/random-query results. Median combined timings: streaming 127.27 ms, buffered 55.58 ms, ordinary mapped 73.86 ms, sealed mapping including setup copy 79.56 ms. **Buffering won this workload.** No claim of generally faster mmap is supported. The [prototype](../prototypes/mapped_data/README.md) remains outside the runtime; encrypted mapped queries, mutable IPC, beyond-RAM stress and real columnar formats remain research work.

## Tooling and delivery

The initial stdio LSP supports bounded local overlays, first-error diagnostics, formatting, symbols and basic completion. Tests caught and repaired missing function symbols and now verify real protocol startup, immutable on-disk sources, stale versions, path/message limits and overlay closure. Checker execution is isolated with time/CPU limits and a Linux memory limit. See [editor limitations](editor.md).

A wheel built and installed in an independent temporary environment outside the checkout. Compiler checking, guard entry point, LSP initialization and LSP checker subprocess passed there. The stdlib synchronization check passed for 20 modules. The separate boundary-probe suite passed 17 tests. Linux/macOS and Python 3.11/3.14 GitHub CI are configured; remote results are reported separately after publication.

## Cross-platform follow-up

The first matrix passed both Linux versions but exposed HTTP readiness failures on macOS. A local regression demonstrated that the inherited HTTP server binding called reverse DNS before becoming ready. GoPyT now binds without that unnecessary lookup; 20 focused HTTP/inventory/security integration tests pass locally. The macOS failure log and before/after DNS regression logs are retained. After the startup fix, the complete local suite passed **645 tests in 234.304 seconds**. The macOS rerun cleared the startup failures and exposed one test portability error: it required TCP_NODELAY to equal 1. Apple's [pinned implementation](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/bsd/netinet/tcp_usrreq.c#L2830) returns a flag mask, with [TF_NODELAY equal to 4](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/bsd/netinet/tcp_var.h#L433). The assertion now checks nonzero, preserving the requirement that Nagle is disabled. A fresh matrix validates both follow-ups. CI now has explicit time limits and verbose test output. An experimental periodic traceback dumper coincided with a Python 3.11 interpreter crash during its dump; that diagnostic timer was removed and its crash log retained.

The expanded audit report later exposed a lock-file creation race during concurrent cold starts. Lock opening now separates existing-file access from exclusive creation and retries creation races within the existing deadline; symlinks, invalid lock files and unlinked directories remain rejected. A fault-injected regression and 36 focused storage/security/network tests pass locally. Reserved-path checks now also reject case variants for case-insensitive filesystems. Fixture-only diagnostics identified the proxy-isolation failure as a TCP reset: the test origin closed without consuming its POST body. The fixture now drains and checks that body and declares its response length. It still requires the direct response and zero proxy hits; the native still rejects reset responses.

## Remaining priority work

Independent security review; user/tenant authorization and gateway integration; safe encrypted-store migration and key rotation; rollback detection; sustained encrypted load and recovery testing; multi-aggregate transaction design; richer editor semantics and dependency support; real-world column datasets and safe encrypted paging. Existing guard finite-case limits remain. No universal security, zero-miss or production-readiness claim is made.
