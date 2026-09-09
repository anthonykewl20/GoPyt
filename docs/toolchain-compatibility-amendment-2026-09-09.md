# Exact toolchain compatibility and explicit upgrades

This amendment changes bytecode from format 2 to format 3 and the exact lockfile
compiler identifier. Language syntax, standard-library declarations, native
signatures and contract evaluation rules are unchanged by this amendment.

## Identity and version policy

The Python distribution's current `0.1.000` version label alone does not establish
runtime compatibility. The exact toolchain ID is now
`gopyt-0.1.000+sha256:<64 lowercase hexadecimal digits>`. Its suffix fingerprints
the installed runtime Python sources, including compiler, stdlib definitions,
natives, VM, contracts, storage, and host tooling. Any byte change, including a
comment, requires rebuilding artifacts and reviewing new locks. This conservative
rule avoids silently treating two source revisions as the same toolchain.

The fingerprint algorithm is SHA-256 over `GoPyT toolchain source identity v1`
followed by a NUL, then records for every immediate `gopyt/*.py` file except
`test_*.py`, sorted by filename UTF-8 bytes. Each record contains the filename
byte length as a little-endian u32, filename bytes, and the 32-byte SHA-256 of its
exact contents. The location, timestamps, platform, bytecode caches, tests and
external package roots do not enter the identity. Regular unpacked source-based
installs are required; stripped-source/zip import installs are not supported.
Restart the process after changing any installed runtime source. This identity
is captured at interpreter startup, not a hot-reload mechanism.

The hash identifies code; it is not a signature or trusted release provenance.
It does not authenticate the publisher or include Python, SQLite, cryptography,
OS libraries, native overrides, environment configuration, or application data.
Use qualified, pinned host environments and the release-integrity controls in
#23. Native signature, bytecode structural, effect and contract validation remain
required even when this fingerprint matches. A malicious artifact author can
copy the public fingerprint, so it must not be treated as authorization.

## Format 3 and loader behavior

The header is magic `GPYT`, u8 version `3`, u8 flags `0`, then the raw 32-byte
toolchain fingerprint. The constant count and all existing format-2 sections
follow, in the same order. Decoding rejects a mismatched fingerprint, unsupported
version, malformed structure or invalid native signature with E100 before
execution. Encoding or constructing a VM from an artifact carrying a different
fingerprint also rejects E100. Host-created Artifact objects default to the
current fingerprint; arbitrary Python host code remains trusted.

Format-1 and format-2 bytecode must be rebuilt from reviewed source; there is no
blind header rewrite or automatic translator. Previous loaders reject format 3.
Two format-3 toolchains with distinct fingerprints reject each other's artifacts.
An identical unpacked source copy or wheel keeps the same fingerprint.

## Upgrade, dependency migration and rollback

1. Retain the old compiler/runtime distribution, its host dependency environment,
   application source, exact lock, artifacts, deployment configuration, and a
   recoverable data backup. Freeze and record their identities before changing
   anything. Follow the storage migration/key procedures for data changes.
2. Install the selected new runtime in an isolated environment. An old lock
   yields E040 with the proposed full canonical lock repair; `check`, `test` and
   `run` do not write that repair automatically. Inspect the toolchain change.
3. For path dependency upgrades, review the new source, spec/contracts, manifest
   version and transitive graph together. Unchanged toolchain plus changed source
   gives E041. Version labels do not bypass digest checks. There is no registry,
   range resolution, implicit dependency download or automatic compatibility claim.
4. Explicitly accept and write the reviewed canonical lock repair. Recompile all
   affected artifacts using the selected runtime. Run package tests, contract and
   native-interface checks, application state oracles and deployment qualification.
   Recompilation is necessary, but is not evidence that an intentional semantic
   change is acceptable to the application.
5. Deploy source/lock/artifact/runtime as a matching unit. If rolling back, restore
   the prior matching unit and its host environment. Never run new artifacts on
   the old VM or rewrite their header to make them load. Restore/migrate data only
   using a separately qualified storage recovery plan. Code compatibility does
   not imply schema or encrypted-state rollback safety (#9/#11/#12/#25).

The existing package version grammar and transitive source digests are unchanged.
Contract changes are source changes and invalidate application locks. The current
stdlib is shipped with the runtime, has no separately resolved version, and is
covered by the runtime fingerprint. Future bytecode layout changes require a
format version change and explicit migration evidence, in addition to the source
identity. No cross-fingerprint semantic compatibility is implicitly promised.

## Qualification status

The focused tests exercise header/version rejection, host-artifact mismatch,
matching source copies, separate-process changed-runtime rejection, unchanged old
locks on E040, explicit lock replacement/rebuild, and rollback to the matching
runtime/artifact. Historical released compiler/runtime combinations, dependency
upgrade scenarios and full release qualification remain required under #22.
This amendment and finite tests alone do not close that issue.
