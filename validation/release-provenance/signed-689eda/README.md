# Signed release after the setuptools update

Source: `689eda92d47a2b846f7ec4cffefda0b7e5a4169a`.
[Signing workflow run](https://github.com/anthonykewl20/GoPyt/actions/runs/34374585088)
completed successfully at this exact revision. This historical evidence covers
the setuptools 84.0.0 build-input update; it does not attest later runtime changes.

The downloaded wheel, reproducibility report and component inventory were again
verified locally with the retained provenance bundle, expected source and signer
workflow, main ref, artifact digests, hosted-runner constraint and current
revocation policy. `recheck/` records fresh verifier results plus verifier/policy
hashes and the exact workflow state. Expected SHA256 values are:

- Wheel: `7e4a4c0064d2181edcfa5d955a28337cc9f44508421a83dd50bc411e50a29c3e`.
- Report: `f346954c7988c2ae7abd286c03cb9abd1f0faa01aba3d09a01e877cf661fe169`.
- Inventory: `2b9e3d16ca419ce723b81bd29d940dd49642c454b877640b6bfd35ee4ed49272`.

Original verification and negative controls are retained alongside the signed
bundle. The inventory tamper control records a verified original and rejected
changed content at the same path. Wheel binaries are not committed here; obtain
the artifact from the named workflow and verify the expected digest before use.

The inventory covers pip 26.2.1, setuptools 84.0.0, cryptography 50.0.1, cffi 2.1.1
and pycparser 3.0 in the measured environment. Its publisher-supplied metadata is
not an independent audit. Other-platform binaries, interpreter-native components,
CI actions and repository adaptations still require separate coverage. This
retention does not complete issue #23's remaining component/adaptation and license
requirements, nor qualify a production release or independent security audit.
