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

## Consolidated release component inventory

`tools/release_components.py` derives one inventory across every class of input
and writes it to `validation/component-review/release-components.json`. Every
identity is read back from a pinned or retained file, never transcribed, so the
inventory cannot drift from what it describes: 5
hash-pinned Python distributions from `requirements/`,
6 checksum-pinned interpreter archives from
`requirements/python-standalone.json`, 4 immutable action
commits read out of the workflows, 70 vendored,
bundled and publisher-SBOM components of the installed distributions, and
54 inputs the interpreter publisher declares.
The derived set carries a digest; `gopyt/test_component_inventory.py` fails when
the inputs and the retained inventory disagree.

Publisher records can be incomplete, and that is recorded rather than smoothed
over. Two components declared no license: `openssl` 4.0.2 inside the cryptography
wheel, and `sqlite` in the interpreter publisher record, which declares linkable
library names but no license field. Both were resolved by downloading the pinned
source archive, comparing its SHA-256 against the identity this repository
already recorded, and reading the license out of the verified archive:
openssl 4.0.2 carries Apache-2.0 in `LICENSE.txt`, and SQLite 3.53.1 carries the
public-domain dedication SPDX records as `blessing`, with its bundled `autosetup`
configurator separately licensed and not linked into the interpreter.
`validation/component-review/resolved-licenses.json` retains each archive digest,
each license-file path and digest, and the opening lines read. Nothing is
inferred from a project name or from another release, and the check rejects a
resolution whose source digest does not match the pinned one.

Any newly undeclared license fails rather than passing silently:
`validation/component-review/unresolved-components.json` is currently empty, and
a component that declares neither a license nor an entry in one of those two
files fails the check.

Which publisher-declared input was actually linked into a selected archive is not
determinable from publisher data, and every such entry says so. Entries that
declare linkable library names are marked as libraries; the rest are build
tooling that is not linked into a release artifact.

## Repository-wide adaptation and license inventory

`tools/adaptation_inventory.py` scans every tracked file for external repository
references and requires each one to be classified in
`validation/component-review/adaptations.json`. A reference that is not
classified fails, and a classification whose reference has disappeared fails too.
The current inventory covers 54 external repositories:
25 build inputs,
11 interpreter inputs,
10 infrastructure references,
4 behavioral references,
3 conceptual prior art, and
1 adaptation. Exactly
1 entry records adopted code: the seal-before-sharing
pattern in the mapped-data research prototype, pinned to its donor revision with
its Unlicense record. Adopted code without a pinned revision or a license record
fails the check.

9 entries stay explicitly unresolved, each naming the
resolution it needs. 5 of them are
interpreter-publisher build tooling: the publisher declares neither a license nor
any linkable library name for them, so they are tools the publisher runs rather
than components linked into an archive, and no license is asserted for them.
0 linked component now lacks a declared
license. The rest are prior-art citations in documents that name a project
without pinning a revision, so no source comparison has been performed against
them. A text scan cannot prove the absence of
unattributed source; this is an inventory of what is referenced and how it was
used, not a license audit, a vulnerability scan or an independent audit. The
hosted-CI trust boundary remains explicit, and this is not an assertion that all
code on a runner is enumerated.

All workflows force-reinstall the hash-pinned build/security wheels, including already-present versions. The standalone archive includes a portable pip launcher whose bytes differ from its packaged RECORD. An ordinary same-version install leaves that launcher untouched. The initial inventory CI rejected it on all four platform/version combinations; reinstalling the approved wheel regenerates consistent entry points and RECORD metadata. The verifier remains strict; the bootstrap mismatch and failed CI are retained under validation/component-inventory.
