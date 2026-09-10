# Release provenance increment

This directory retains the manual main-only signing workflow, consumer identity
checks and source/artifact withdrawal evidence. Initial-stage notes below are
historical; signed end-to-end runs are linked at the end. Issue #23's complete
component/adaptation inventory remains open.

- `source.json` freezes the runtime, tests, verifier, workflow and revocation file.
- `unit.log` records six local rejection/identity-control tests. Signature calls
  are mocked there; those tests do not establish cryptographic verification.
- `python31116.log` records 15 release-build/interpreter/provenance tests on the
  positively hash/version-verified local CPython 3.11.16 distribution.
- `full.log`, `guard.log`, `stdlib.log` retain regression and repository checks.
- `action-review.json` records immutable wrapper/implementation identities and
  inspected metadata/README blobs; the implementation uses Node24.
- `verifier-version.log`, `verifier-help.log` record the independently installed
  GitHub CLI and its source/signer/digest/ref/bundle policy capabilities.
- `workflow-parse.log` includes a failed inspection assumption about step count.

Actual signed-bundle verification and wrong-source rejection are workflow gates
that still require execution after merge. GitHub/Sigstore and the verifier remain
explicit trust dependencies. No runtime dependency or compiler source changed.

Final local full suite: 762 tests passed in 303.091 seconds. All frozen source hashes matched after completion. Guard calibration and stdlib parity passed.

The signed-368ad3 directory now retains actual signed-run and separate local verification evidence. It qualifies wheel and report signing at that source; it does not attest the later component-inventory feature. Earlier pending statements above describe the initial implementation stage.

The [signed-689eda report](signed-689eda/README.md) retains the signed wheel, report
and inventory verification from the setuptools 84.0.0 build-input revision,
including a fresh local verification of all three expected artifact identities.
It is evidence for that source revision, not an attestation of later runtime
changes. The complete component, license and adaptation coverage remains open.
