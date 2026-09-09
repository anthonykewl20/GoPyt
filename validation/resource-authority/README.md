# Resource authority validation

Implementation base is runtime repair `5a343af` (now merged in PR #28 as
`7c66e048ba66a8c8d29dc01ac4a736ae05c1abdc`). The implementation and tests are
committed together; historical audit results retain their own source identities.

- `focused-python311.log`: 18 new authority tests and five existing operator DB
  policy tests, all passed on Python 3.11.15.
- `language-python314-initial.log`: 691 language tests passed before adding
  secret/evolution cases. `language-python314.log` is the final fixed-source run: 693 tests passed
  in 196.131 seconds, with all 87 source hashes unchanged.
- `focused-python314-final.log`: final 18-case authority run also covers the
  last bounded-batch admission check (added after the repeated full run started).
  The final fixed-source full suite also includes that check; GitHub CI will
  additionally verify both supported platforms and Python versions.
- `focused-python311-initial.log`: first 21-case run (16 new plus five existing);
  `focused-python311.log` repeats with secret/evolution tests (23 cases).
- `guard.log`, `stdlib.log`, `demo.log`: unchanged Guard boundary tests, closed
  stdlib parity, and the real contract-drift example.
- `wheel.log`, `wheel-smoke.log`: build and clean-install checks from the actual
  uv-managed Python 3.14 interpreter, outside the source checkout.
- `fixture-*-failure.log`: retained fixture development failures. The first
  fixture duplicated a module use declaration, the second omitted parallel's
  time effect, and the network fixture omitted remote-model network effects and
  had unused imports in its server spec. These are compiler-rejected fixtures,
  not security findings or runtime passing evidence; they were corrected before
  testing authority behavior.

Review covered every current resource native and both thread creation paths
(parallel children and HTTP workers). The finite test scope and explicit host
trust boundaries are in `docs/resource-authority-amendment-2026-09-09.md`.

No new dependency or throughput/production qualification claim. Earlier repair
README counted six existing DB policy tests; its correction to five matches its
unaltered 11-test raw log (six new + five existing).

The intermediate `language-python314-source-change-failure.log` correctly
rejected a trusted Guard engine identity because the batch admission guard was
edited while that run was live. Runtime/tests were then frozen, hashes recorded,
and the entire suite rerun successfully. No failing verification was waived.
Final Python 3.11 focused verification (23 tests) and wheel/install checks use
the `*-final.log` paths. Earlier runs are retained separately.
