# Descriptor integration qualification

Reviewed source: 392436bb2268a08d97f483e573ff5e552e32483b. Runtime and individual
source hashes are retained in manifest.json and source.json. All source hashes
were verified unchanged after the terminal full-suite result.

- Python 3.11.16: full language regression, 996 tests passed in 400.790 seconds.
- Python 3.11.16/3.14.7: final socket/TLS and compiled outbound tests, 20 passed each.
- Python 3.14.7: Guard calibration, 17 passed; stdlib parity, 22 modules; contract demo passed.
- Two runtime wheels built with epoch 1788998400 were bitwise identical, containing
  56 runtime files. Installed wheel smoke and old/new/rollback verification passed.

The full-suite command was `python -u -m unittest discover -s gopyt -t . -v`.
Other commands follow .github/workflows/validate.yml, using the already-built
baseline wheel for upgrade verification. The wheel hash is in manifest.json.
The initial TLS handoff log retains the ResourceWarning which led to the alias
cleanup fix; the strengthened final tests retain the alias and verify invalidation.

This is local Linux evidence. Full Python 3.14 regression and exact-head platform CI
have not been run for this descriptor branch. The native deadline inventory still
needs refresh. Descriptor integration does not complete resolver/trust-store,
SQLite/TLS/encryption native-memory accounting, or issue 5's full acceptance scope.
