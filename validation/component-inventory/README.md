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
