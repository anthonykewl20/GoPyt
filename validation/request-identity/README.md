# Request identity validation

Base: `ed1a9d720ce81053a933da9700f1d3b0929f86f4` (resource authority PR #29,
now merged). The runtime/test source snapshot is `source-sha256.json` (89 files),
recorded before the final full-suite run. Environment details are retained for
Python 3.14.7 and 3.11.15, both using the existing security extra 50.0.1.

- `focused-python314.log`: 22 identity cases plus 18 existing resource authority
  cases. Includes real HTTP under development and strict/encrypted profiles.
- `focused-python311.log`: the same 40 cases on Python 3.11.15 with the security
  extra installed in an isolated uv tool environment; no tests skipped.
- `language-python314.log`: final full language regression run on frozen sources: 715 tests passed
  in 252.766 seconds. All 89 source hashes remained unchanged afterward.
- `guard.log`, `stdlib.log`, `demo.log`: 17 Guard boundary checks, stdlib parity,
  executable contract-drift demo.
- `wheel.log`, `wheel-smoke.log`: runtime wheel build and clean install outside
  the checkout using the actual uv-managed Python 3.14 interpreter.
- `fixture-parallel-first-failure.log`: initial parallel identity fixture used a
  one-character tenant `a`, correctly rejected by the specified snake grammar;
  corrected to `alpha` and explicit expected resource denial before final runs.
- `python311-missing-extra-first.log`: also records the first Python 3.11 run
  lacking cryptography. The typed security configuration failure was expected
  for that environment; reran with the repository's pinned optional extra.

No production timing/data claims or new dependency. The same generated HTTP
cases are intentionally repeated under two security profiles; they are not two
independent reviewers or independent datasets. Source review and explicit limits
are in `docs/request-identity-amendment-2026-09-09.md`. Independent security and
deployed gateway/MFA/IdP qualification remain outside these finite regressions.

`acceptance.json` preserves all 14 original acceptance/evidence criteria for
issues #4/#10 and maps each to source/tests/docs. Its final publication gates
require merged supported-platform CI and verified remote issue/tracker updates.
