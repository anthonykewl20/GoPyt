# Release input component inventory

`tools/component_inventory.py` inventories the five selected installed inputs:
pip, setuptools, cryptography, cffi and pycparser. They retain their existing
build/security roles; the collector adds no dependency. It verifies every hashed
installed file against its RECORD digest and size and refuses missing/modified
files. Only bytecode cache files and RECORD itself may lack hashes. RECORD is
not a signature; the preceding hash-pinned wheel installation supplies the
artifact identity. A compromised host can replace both code and its RECORD.

The output preserves upstream Requires-Dist metadata, nested vendored-package
METADATA, exact vendor.txt pins, license text and hashes, and publisher-provided
SBOM documents. Upstream extras in metadata are not GoPyT dependencies. The
collector does not import or execute vendor packages to discover their versions.
It fails on unrecognized vendor declarations rather than silently omitting them.

CI retains the report with the reproduced wheels on each supported platform.
The release workflow also attests the inventory file. Consumers must verify that
file's attestation against the same approved source/workflow identity before
trusting it as a release record. A wheel's attestation alone does not authenticate
an adjacent inventory file.

The local sample reports 18 pip vendor pins, 12 setuptools bundled METADATA
records, and two cryptography SBOMs (including publisher-supplied Rust/native
component descriptions). Those are evidence from the selected installed wheels,
not independently verified completeness or licensing conclusions. Counts can
change only with reviewed input updates; downstream tooling should use identities
and preserved documents rather than hard-coded counts.

## Remaining inventory scope

Publisher metadata can be incomplete. Native code in cffi and cryptography needs
its relevant build/SBOM provenance and license review for each platform. CPython
and its native libraries, the hosted bootstrap, bundled action JavaScript/Node
components, and repository source adaptations require their own records. The
hosted-CI trust boundary remains explicit. This report is not an assertion that
all code on a runner is enumerated, a vulnerability scan, or an independent audit.

This increment advances #23; the complete inventory criterion remains open.

All workflows force-reinstall the hash-pinned build/security wheels, including already-present versions. The standalone archive includes a portable pip launcher whose bytes differ from its packaged RECORD. An ordinary same-version install leaves that launcher untouched. The initial inventory CI rejected it on all four platform/version combinations; reinstalling the approved wheel regenerates consistent entry points and RECORD metadata. The verifier remains strict; the bootstrap mismatch and failed CI are retained under validation/component-inventory.
