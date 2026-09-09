# Release provenance and withdrawal policy

GoPyT release candidates must identify a full source commit and artifact SHA256.
The release workflow can run manually only on this repository's `main` branch.
It requires the latest push validation run for that exact commit to have completed
successfully, then reproduces the wheel with reviewed inputs and exercises a clean
installation before signing the wheel and build report. It does not upload to
PyPI, create a GitHub Release, or treat an artifact from a failed run as approved.

## Trust and permissions

`.github/workflows/release.yml` uses GitHub-hosted Ubuntu 24.04, the existing
checksum-pinned Python/build inputs, and immutable actions. The attestation action
v4.2.2 is a composite which delegates to the immutable Node24 `actions/attest`
v4.2.1 revision recorded in the review evidence. It uses GitHub OIDC and Sigstore
attestations rather than a stored project signing key. Only this job receives
`id-token: write` and `attestations: write`; validation jobs remain read-only.
No checkout credentials persist. The signing job also needs Actions read access
to enforce its exact-commit validation gate.

The trusted computing base includes repository maintainers, workflow source,
GitHub's hosted runner/bootstrap/actions/OIDC infrastructure, Sigstore trust
roots and the consumer's independently installed GitHub CLI. An attacker who can
modify the approved main source or compromise the signing job can create signed
malicious output. The signature proves attributed provenance, not code safety,
independent review, or hermetic execution. This workflow is not an isolated
reusable trusted builder and makes no SLSA level claim.

## Consumer verification

Obtain the expected commit and artifact SHA256 through an independently trusted
release record. Obtain a current `release/revocations.json` from trusted main;
a copy bundled with an old artifact cannot prove that the release is still
accepted. Preserve the verified bytes in a directory other processes cannot
modify between verification and installation.

Use an independently trusted GitHub CLI that supports all the identity flags in
`tools/verify_release.py`. Never run an unverified artifact to verify itself.
From a trusted source checkout, run:

```sh
python3 tools/verify_release.py candidate.whl \
  --source-sha FULL_APPROVED_COMMIT \
  --sha256 APPROVED_ARTIFACT_SHA256 \
  --revocations /trusted/current/revocations.json \
  --bundle /downloaded/provenance.jsonl
```

The verifier fails on a missing/malformed revocation policy, withdrawn source or
artifact, wrong bytes, absent/invalid signature, incorrect source or signer
commit, wrong repository/workflow/ref, or self-hosted signer. It delegates actual
certificate, signature, transparency and predicate verification to `gh attestation
verify`; it does not interpret an unsigned JSON claim as cryptographic proof.
Omitting `--bundle` fetches the attestation using GitHub. Offline bundles do not
remove the need for trusted verifier roots and current withdrawal information.

Successful workflow artifacts retain the wheel, both reproduction trials,
source/environment report, validation run identity, attestation bundle, positive
verification and negative controls for 90 days. A release promoted for longer
support must copy these exact verified artifacts and records to durable release
storage before expiration. A green validation run without an attestation is a
build candidate, not authenticated release provenance.

## Vulnerability response and revocation

Report vulnerabilities through private GitHub Security reporting as described in
SECURITY.md. The maintainer handling the report records affected source commits,
artifact digests, bundled component versions, exploit conditions and mitigations
privately. Do not put exploit details or credentials into public issue bodies.
There is no staffed response-time SLA for this experimental project.

For a credible compromise or unsafe release, stop promotion and add its digest to
`release/revocations.json` under `artifacts`, or its full source commit under
`sources` to withdraw every artifact from that source. Values are nonempty public
reasons, such as an advisory identifier; keep sensitive details private until
coordinated disclosure. Review and merge the update promptly. Keep withdrawn
identities in the history and active list; deletion of an attestation alone is
not revocation because downloaded bundles survive.

Inventory the affected dependency and bundled copies, assess deployments, and
provide an advisory with affected identities and mitigation or replacement.
Prepare a separately reviewed fix with new identities, repeat supported-platform
validation, reproduction, installation and signing, then verify the replacement
before promotion. Record failed trials and the reason for withdrawal. Do not
reuse a digest label, silently replace hashes, or promise that installing an old
compiler safely rolls back application data. Storage recovery follows its own
qualified backup/migration procedure.

## Qualification status

Local policy regression tests cover rejection behavior. Actual signed workflow
execution, downloaded-bundle positive verification and wrong-source rejection
must be retained after the workflow reaches main. Until that happens, signed
end-to-end qualification is pending. Complete bundled component and repository
adaptation/license inventories remain separate unfinished parts of #23.

Reference: [GitHub artifact attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations).
