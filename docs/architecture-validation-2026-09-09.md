# Atomic data-backend increment: measured acceptance

This increment adds atomic multi-key updates and reads, operator database
namespace authority, bounded authenticated key rotation, and a typed HTTP
acceptance application. It does not complete the
[architecture programme](architecture-programme.md) or establish production readiness.

## Implemented behavior

- A command's idempotency marker and related data changes can commit together.
  Failed expectations change no keys. Batch reads observe one consistent state.
  Deletion, absence, unchanged conditions and bounded input/results are explicit.
- Operator namespace policies are checked before storage and cache access.
  Conditional batches require read and write authority for every key.
- A private keyring retains up to four authenticated decryption keys while
  writes select one active key. Explicit maintenance rekeys the whole snapshot.
- The runtime wheel excludes test modules, including stale generated modules
  from a reused build directory. Installed compiler, LSP, Guard, storage
  administration and compiled transactions pass outside the source checkout.

The 64 MiB whole-snapshot backend remains. New interfaces do not turn it into
a scalable database engine. Resource lifetimes, native execution, broad
capability delegation, user/tenant identity, rollback protection, streaming data,
stateful Guard integration and richer editor semantics remain open.

## Public real-data evidence

Source: Daqing Chen (2015), [Online Retail, UCI](https://archive.ics.uci.edu/dataset/352/online+retail),
[DOI](https://doi.org/10.24432/C5BW33), CC BY 4.0. The pinned workbook contains
541,909 rows, 25,900 invoice identifiers and 4,070 stock codes, including 10,624
negative-quantity rows and 9,288 cancellation rows. No source row was dropped.
The expected total quantity is 5,176,450.

The host projects source identifiers and quantities, omitting customer IDs and
unneeded fields. Replay uses bounded ingestion batches that may split original
invoices. Expected SKU totals come from a separate SQLite aggregation. The v2
[dataset manifest](../validation/architecture/dataset-manifest-v2.json) pins the
workbook, replay corpus, expected JSON and SQL oracle. The first preparation
pinned the corpus and expected JSON but omitted the SQL file; the full HTTP and
single-writer trials use the strengthened v2 preparation. Earlier evidence is retained.

| Trial | Original rows | Committed batches | Duplicate/stale batches | Wall time | Outcome |
| --- | ---: | ---: | ---: | ---: | --- |
| [Plaintext, 4 processes](../validation/architecture/retail-plaintext-4/report.json) | 541,909 | 4,234 | 4,234 | 79.42 s | Exact totals, restart verified |
| [Encrypted, 4 processes](../validation/architecture/retail-encrypted-4/report.json) | 541,909 | 4,234 | 4,234 | 96.25 s | Exact totals, restart verified |
| [Encrypted, 8 overlapping processes](../validation/architecture/retail-encrypted-overlap-8/report.json) | 541,909 | 4,234 | 29,638 | 150.91 s | Exact totals, one application per batch |
| [Encrypted, 1 process, v2](../validation/architecture/retail-encrypted-1-v2/report.json) | 541,909 | 4,234 | 4,234 | 67.84 s | Exact totals, zero conflicts |
| [Authenticated HTTP, encrypted, v2](../validation/architecture/retail-http-full-v2.json) | 541,909 | 8,468 | 8,468 | 161.35 s | Exact totals after forced server restart |

Each trial retains its exact source hashes. Early four-process trials precede
the HTTP/JSON additions; the eight-process and final v2 trials include them.
VM trials use compiled GoPyT arithmetic, reads and commits, with host-side row
streaming/grouping. The HTTP trial calculates quantity updates in its host
driver and exercises GoPyT typed JSON, authorization boundary and transactions
through real connections. It additionally checks malformed JSON, missing
credentials, a denied cross-namespace batch and 24 concurrent duplicate requests.

VM wall times include worker startup and all replay passes, but exclude source
preparation, compilation and final verification. HTTP time covers ingestion
and stale retries, excluding startup, boundary checks and restart verification.
They measure different workloads and are not directly comparable.

The single-writer run had no CAS conflicts; four encrypted writers had 7,550.
Its combined original/duplicate batch latency was p50 7.17 ms, p95 16.69 ms and
p99 21.76 ms. Maximum individual worker peak RSS in the full VM trials was
approximately 40 MiB, not total process-group memory. Final aggregated snapshots
were approximately 0.52 MiB: replaying many source rows is not evidence of a
large or beyond-RAM stored database. The [host observation](../validation/architecture/environment.json)
shows a shared, loaded machine; these are exploratory trials, not isolated SLO
qualification or a proven scaling curve. Increasing writer count did not improve
this workload; serialized ingestion is the better starting configuration here.

## Regression and fault evidence

- [669 language tests passed](../validation/architecture/language-tests.log),
  including 1,000 seeded transaction sequences checked against an independent
  dictionary, competing-process transfers and compiled native calls.
- [17 Guard boundary probes passed](../validation/architecture/boundary-probes.log).
  This does not expand Guard's finite pure-function acceptance scope.
- Tests inject failures before publication, after replacement at directory
  fsync, and process death before/after replacement. No torn batch was observed.
  A failed operation can have committed; the API documentation preserves that
  ambiguity. These are not physical power-loss tests.
- Key tests cover old-key retirement, cached-read invalidation, corrupted
  ciphertext, failed publication, successful retry and encrypted batch restart.
- [Installed wheel checks passed](../validation/architecture/wheel-smoke-final.json);
  [editable installation](../validation/architecture/editable-install.log),
  stdlib synchronization and the contract-drift demo also passed locally.

Retained authoring failures include incorrect fixture syntax/imports and the
stale-build wheel inclusion bug. No acceptance result was edited to turn a failure
into a pass. Source changes and successor trials are separate evidence.

The manual `real-data-qualification` workflow reproduces encrypted replay,
eight-process duplicates and HTTP restart checks with SHA-pinned downloads and
retained artifacts. Adding the workflow is not evidence that its remote run has
passed. No production deployment, independent security audit, fresh VulcanBench
score, native backend or beyond-RAM qualification is claimed.
