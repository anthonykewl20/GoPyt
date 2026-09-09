# Atomic batches and operator database authority

This amendment extends the closed `store.db` declarations in `docs/stdlib.md`.
The existing bytecode record/list/option forms encode the new types; no opcode
or file-format change is introduced. Native declarations remain loader-verified.

`Change` contains `key: str`, `expected: str?` and `value: str?`.
`Snapshot` contains `values: list[str?]`.

`get_many(keys)` reads all keys from one locked snapshot, in the supplied order.
An absent key is `none`; an empty string is `some("")`. It requires
`database.read`. Separate `get` calls do not provide this guarantee.

`compare_exchange_many(changes)` requires `database.read` and `database.write`.
Every expectation is tested against the same pre-update snapshot. A mismatch
returns `false` without publishing any change. When every expectation matches,
all replacements/deletions commit together and the operation returns `true`.
`value: none` deletes an existing key; absent-to-absent is a no-op. An unchanged
expected/value pair is a read condition and still participates in conflict
checking. A wholly unchanged batch returns `true` without rewriting the file.

Both calls require 1..256 distinct, nonempty keys. Duplicates, malformed input,
more than 8 MiB of aggregate UTF-8 input, or a batch-read result over 8 MiB
return `DbError`. Inputs are frozen before lock acquisition. Limits do not
change the 64 MiB persistent snapshot bound or introduce a streaming database.

The existing lock, authenticated load, single-file publication, file fsync and
directory fsync protocol applies to the complete batch. Cooperating processes
serialize at that lock. Failure after replacement but before successful
directory fsync has an ambiguous commit outcome and returns `DbError`; callers
must reconcile state, not assume rollback. Cancellation after a native write
can similarly hide a committed result. An idempotency key and all its business
changes should participate in the same batch. CAS remains susceptible to ABA
unless the application includes its own version field in the compared value.

## Namespace authority

The trusted host may set `GOPYT_DB_POLICY_FILE` to a private, single-link file
outside the application package, subject to the secret-file path rules.

```json
{"version":1,"read":["tenant/acme/"],"write":["tenant/acme/"]}
```

Grants are literal string prefixes ending in `/`; `tenant/acme/` does not grant
`tenant/acme2/`. No path normalization is applied to KV keys. `*` is an explicit
all-key grant. Empty lists deny the corresponding operation. Each list is
bounded to 256 entries, each at most 256 characters; the file is at most 64 KiB.
Malformed, duplicate-field, missing, or insecure configured files fail closed.
An unset variable retains existing package-wide access, including in strict
mode; adopting a namespace policy is a separate deployment decision.

Authority is read and checked before opening storage or returning cached data.
A conditional operation requires read and write authority for every key. A
denied batch changes nothing and returns no partial read result. Configuration
changes affect subsequent operations; an operation already admitted may finish.
This confines a service, not individual HTTP users. It does not add tenant
authentication, resource delegation, or an OS sandbox.

## Validation and references

Tests in `gopyt/test_storage_transactions.py`, `gopyt/test_capabilities.py` and
`gopyt/test_key_rotation.py` exercise conflicts, absence, deletion, malformed
input, bounded responses, independent state transitions, competing processes,
publication failures, encrypted restart, policy revocation and compiled calls.

Publication ordering was cross-checked against SQLite's
[atomic commit explanation](https://www.sqlite.org/atomiccommit.html) and the
Leitir-discovered [nq source](https://github.com/leahneukirchen/nq/blob/8bad1d011bc1da3c7c8cf58eb7cf19481a73de9d/nq.c#L298)
at commit `8bad1d011bc1da3c7c8cf58eb7cf19481a73de9d`, especially exclusive
creation, flock, rename and directory fsync. The latter is queue code, not a
database proof. No donor code or dependency was installed into GoPyT.
