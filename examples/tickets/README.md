# Support-ticket API acceptance contract

This is the first GoPyT application-validation workload. It runs entirely as
GoPyT application code. The external acceptance runner owns its expected results.

Start a copied working package with `gopyt run tickets.serve`. The runtime uses
`GOPYT_HTTP_ADDR` (default `127.0.0.1:8080`). No authentication is provided; this
is a local validation app, not an Internet-ready ticketing product.

## HTTP contract

All application outcomes use HTTP 200 and exactly this JSON envelope:
`{"outcome":"ok","id":"ticket_1","title":"Example","status":"open","version":1}`.
This follows v0's fixed HTTP mapping. Malformed JSON/schema is HTTP 400 with an
empty body; unmatched routes are HTTP 404. No custom status mapping is added.

- `POST /tickets`, body `{ "id": str, "title": str }`: atomically creates one
  ticket with status `open`, version 1, outcome `created`. Duplicate ID returns
  `conflict` with the current ticket, preserving the original title/state.
- `GET /tickets/{id}`: returns `ok` with the stored ticket, or `not_found`.
- `POST /tickets/{id}/transition`, body
  `{ "expected_version": i64, "status": str }`: requires the current version.
  Only `open` → `in_progress` → `closed` is allowed. Success returns `updated`
  with the new status and version incremented by exactly one. No reopening,
  skipping states, or same-state update. Stale version returns `conflict` with
  the current ticket. Racing updates from one version have exactly one winner.

IDs contain 1–64 ASCII letters, digits, underscores, or hyphens. Titles contain
1–200 Unicode codepoints (whitespace is accepted). Status is exactly `open`,
`in_progress`, or `closed`. expected_version must be positive. Invalid fields
return `invalid` before storage access. For transitions: field validation comes
first, then lookup, then version comparison, then allowed-transition validation.

For `invalid`, `not_found`, or `storage_error`, id is the requested id and title
and status are empty strings, with version 0. Storage failures are never reported
as successful writes. All responses contain exactly the five envelope fields.
Malformed stored records return `storage_error` rather than inventing a ticket.

Acknowledged writes survive process restart and process kill. Creation and update
are atomic across cooperating processes sharing a package. Corrupt SQLite files,
unexpected database schemas and invalid stored ticket records are diagnosed.
Valid external changes are not authenticated; removing the database initializes
empty storage. Hardware power-loss certification is outside this local test campaign.

## Persistence and repeatable tests

Storage lives in the runtime's package-local `.gopyt-state` state directory (see the
storage amendment). Run acceptance and benchmarks on disposable copies. Tests
must not delete or change another package's state. Pure language tests cover
validation; independent HTTP checks cover creation, persistence, and races.

## Scope

Listing/search, deletion, authentication, attachments, and notifications are not
part of this workload. The quotation service and integration worker are later
application-validation stages. Results and feature proposals are documented
separately from this acceptance contract.
