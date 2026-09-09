# Component review records

These are review inputs for #23, not a completed SBOM or a new dependency list.

- `python-publisher-ref.json` pins python-build-standalone release 20260901 to
  commit `4bb01f09aaf362c71e891be4a41cb6d6ddf830b3`.
- `python-publisher-downloads.json` preserves that publisher's DOWNLOADS data,
  parsed as literals without importing or executing donor code. It includes
  platforms and tools that GoPyT does not use. A declared download is not proof
  that a component was linked into a particular selected archive.
- `identities.json` identifies inspected publisher download/license source blobs.
  The source file carries an MPL-2.0 notice; this record attributes its metadata
  to astral-sh/python-build-standalone. No build code was transplanted.
- `action-record-paths.json` records license and package metadata paths at the
  immutable selected action commits. Tree responses were untruncated. Matching
  blob identities identify review material, not independent code/license audit.
- `source-references.json` lists references found in the tracked project documents
  and the existing release ledger. It distinguishes adapted patterns, behavioral
  references and conceptual prior art. Unpinned or unresolved entries remain
  explicit; a text search cannot prove absence of unattributed source.

The publisher data names CPython 3.11.16/3.14.7 and prospective native inputs with
versions, hashes and license identifiers. Platform selection/linkage and native
wheel components still require reconciliation. Existing interpreter archive pins
and installed wheel reports provide complementary artifact identities.

CI actions also carry package locks. Their entries not marked dev currently
number 24/50/156/148 for checkout/setup-python/upload-artifact/attest. This alone
is not a statement that all those entries execute or appear in the bundled action.
The wrapper attest-build-provenance delegates to the separately pinned attest
implementation. Do not adopt upstream development dependencies from these records.
