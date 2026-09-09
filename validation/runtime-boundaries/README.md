# Audit R1–R3 repair evidence

Base: `7db7e16`, the pending architecture review PR #27; runtime source matches
merged main `e2537c1`. These results qualify only the repairs described in
`docs/native-boundary-amendment-2026-09-09.md`, not whole milestone issues.

- `before-python314.log`: first five new tests before implementation; absent
  provider and injected parser recursion error, maximum sleep fails wait bound.
  Deep real JSON is already normalized on Python 3.14 as the audit reported.
- `focused-python311.log`: six new compiled regressions plus five existing
  authority tests, including the real deeply nested input, pass on Python 3.11.15.
- `language-python314.log`: full language regression command
  `python3 -u -m unittest discover -s gopyt -t . -v` on Python 3.14.7: 675 tests passed in 205.057 seconds.
- `guard.log`: 17 Guard boundary tests.
- `stdlib.log`, `demo.log`: stdlib parity and executable contract demo.
- `wheel.log`: built runtime wheel, with artifact SHA256 in output.
- `wheel-smoke.log`: retained first failure; the local relocated Python launcher
  creates a venv whose stdlib incorrectly resolves to `/install/lib/python3.14`.
- `wheel-smoke-python311.log`, `wheel-smoke-python314.log`: same wheel passed
  clean install and external-checkout entry-point/transaction checks using the
  actual uv-managed interpreter paths instead of the relocated launcher.

Interpreter roots are under `/home/soultransit/.local/share/uv/python/`:
`cpython-3.11.15-linux-x86_64-gnu/bin/python3.11` and
`cpython-3.14.7-linux-x86_64-gnu/bin/python3.14`.
No new dependencies or performance improvement claims accompany these fixes.
