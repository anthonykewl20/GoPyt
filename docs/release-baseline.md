# GoPyT language baseline: 2026-09-09

The language baseline is published and its complete Linux/macOS CI matrix passed at commit `90505c0bef75d1cb9414c4e9cb62d71a9483a921`. This is an alpha engineering release, not a security certification, a new VulcanBench model score, or a proof of arbitrary application correctness.

## Final validation

[Successful GitHub run](https://github.com/anthonykewl20/GoPyt/actions/runs/34272736437). Its [metadata](../validation/release/github-ci.json) and [full logs](../validation/release/github-ci.log) are retained in the repository.

| Platform | Language suite | Boundary-probe suite | Remaining CI checks |
| --- | --- | --- | --- |
| Linux, Python 3.11 | 646 tests, pass | 17 tests, pass | Pass |
| Linux, Python 3.14 | 646 tests, pass | 17 tests, pass | Pass |
| macOS, Python 3.11 | 646 tests, 2 Linux-only prototype skips, pass | 17 tests, pass | Pass |
| macOS, Python 3.14 | 646 tests, 2 Linux-only prototype skips, pass | 17 tests, pass | Pass |

Remaining checks include synchronization of 20 stdlib modules, the contract-drift demo, wheel construction and installation in a fresh environment outside the checkout. The installed compiler, guard entry point, LSP initialization and LSP checker subprocess all passed. The final local full suite also passed 646 tests in 180.927 seconds.

## What changed

The inventory backend implements a bounded reservation ledger with explicit HTTP/JSON, storage and clock integration. Identical retries retain their outcome; changed quantities or TTLs conflict; confirmation is terminal; cancellation and expiry restore stock; retained IDs prevent reuse. Whole-state compare/exchange prevents lost updates. The example intentionally caps stock at 10 units and retained reservation IDs at 32.

Correctness tests compare 400 seeded state transitions against an independent Python model and exercise eight real HTTP lifecycle/concurrency/restart scenarios. Generated-program tests compare 48 arithmetic/branch programs at 11 inputs each, including signed 64-bit overflow, against an independent integer oracle. These are constructed workloads, not customer production data or exhaustive program proofs.

Security work repaired a demonstrated hard-link write outside an application package, added case-folded reserved-path protection, and hardened concurrent lock-file creation. The opt-in strict profile requires private external credentials, authenticated HTTP on a loopback listener behind a TLS gateway, and authenticated encrypted storage through AES-256-GCM-SIV. Tests cover unauthorized and duplicate credentials, ciphertext tampering, wrong/missing keys, changed identities, cached reads, refusal of implicit plaintext migration and encrypted restart. See [SECURITY.md](../SECURITY.md) and the [host-profile amendment](security-profile-amendment-2026-09-09.md).

The security extra is an explicit project dependency choice, independent of Leitir source discovery. A [pip-audit scan](../validation/release/dependency-audit.json) found no known advisories for its three pinned packages at execution time. That scan is not an independent code audit. Private vulnerability reporting and dependency alerts are enabled on GitHub.

The initial LSP provides bounded local document overlays, first-error diagnostics, formatting, symbols and basic completion. Tests repaired missing function symbols and exercise actual stdio framing, source preservation, stale versions, path/message bounds and overlay closure. Compiler checks use a subprocess with time/CPU limits and a Linux address-space limit. See [editor limitations](editor.md).

HTTP startup now binds without an unnecessary reverse-DNS lookup, eliminating the readiness failures observed on macOS.

## Measured performance

Three alternating before/after inventory runs measured 40 GET requests per run:

| Variant | Median of run medians |
| --- | ---: |
| Unconditional snapshot write | 6.813 ms |
| Skip unchanged snapshot write | 4.516 ms |

The latency ratio is **1.51x** on this local workload. An 80-request/eight-worker run measured **44.52 ms p95**. [Raw timings and states](../validation/release/inventory.json) are retained. These development-profile measurements exclude encryption overhead, sustained saturation, multiple machines and power-loss testing. The shared host was not an isolated benchmark machine.

## Mapped-data research

Leitir's bounded global GitHub searches found a verified commit of `a-darwish/memfd-examples`. Its inspected Unlicense permitted adaptation of the seal-before-sharing pattern. The local prototype also seals growth and validates exact format and index bounds. No donor package became a dependency.

Twelve subprocess runs over a synthetic 8 MiB i64 column matched independent sequential/random-query results. Median combined timings were streaming 127.27 ms, buffering 55.58 ms, ordinary mapping 73.86 ms and sealed mapping including its setup copy 79.56 ms. **Buffering won this workload.** The [prototype](../prototypes/mapped_data/README.md) remains outside the language runtime. This does not measure encrypted queries, mutable IPC, beyond-RAM data, Parquet decoding or GPU operations.

## Retained R&D findings

Earlier failures remain under [validation/release](../validation/release/). They include the original hard-link reproduction, macOS readiness failures, concurrent lock initialization, an opaque diagnostic-audit failure, and an interpreter crash during an experimental periodic traceback dump. CI retains execution limits and verbose output; the periodic dumper was removed.

Two test assumptions were corrected with evidence. Darwin returns a nonzero TCP_NODELAY flag mask rather than Linux's integer 1; the assertion now checks enabled/nonzero. Apple's [pinned source](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/bsd/netinet/tcp_usrreq.c#L2830) and [flag definition](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/bsd/netinet/tcp_var.h#L433) establish that behavior. The proxy-isolation fixture now consumes and verifies its POST body and declares response length, avoiding a peer reset from closing over unread input. It still requires the direct response and zero proxy hits; the native continues to reject reset responses.

## Remaining priorities

Independent security review; user/tenant authorization and gateway integration; encrypted-store migration and key rotation; rollback detection; sustained encrypted load and recovery testing; multiple-aggregate transactions; richer editor semantics and dependencies; real column datasets and safe encrypted paging. Existing guard finite-case limits remain. This release makes no universal-security, zero-miss or production-readiness claim.
