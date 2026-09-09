# Installed component inventory evidence

`local.json` was collected from the previously hash-installed release environment
on local Python 3.14.7. Installed files were checked against RECORD before vendor
pins, nested metadata, licenses and SBOMs were retained. The report documents its
limits and preserves upstream extras without adopting them.

`unit.log` covers changed/missing/unhashed code, nested records and unknown vendor
syntax. `source.json` freezes runtime, tests, inventory tool and affected workflows
before full regression. `full.log` retains the complete regression run.

Cross-platform reports and an attested inventory still require the new workflow
revision in CI. These local tests do not prove native-component completeness,
publisher authenticity, legal compatibility, or absence of vulnerabilities.

All 767 local regression tests passed in 307.167 seconds. Frozen hashes matched after completion. Five focused tests also passed on Python 3.11.16. Both edited workflows passed YAML and shell syntax checks.

Initial CI failed on every matrix entry because the hash-approved standalone archive ships a portable pip launcher with stale RECORD metadata. ci-failure.log and bootstrap-record-mismatch.json retain the evidence. All workflows now force-reinstall the hash-pinned wheels. reinstall31116.log and reinstalled31116.json record a successful real installation/inventory on that same interpreter. reinstall-source.json identifies the repair; prior full.log/source.json describe the initial revision. No collector/runtime checks were weakened or changed. The repaired workflow matrix is pending CI.
