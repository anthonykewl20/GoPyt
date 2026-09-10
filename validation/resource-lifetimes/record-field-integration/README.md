Record and enum field admission integration evidence

Frozen source/runtime identity is in source.json (head 424a915, runtime
sha256:349c843059365a2799c8ac09906db7cc56642de338f60341299bd50e3f97bad4).
581 tracked source files are hashed there.

Corrected Python 3.14.7 full language suite: 1,133 tests passed in 518.927s
(full314.log). Corrected Python 3.11.16 full language suite: 1,133 tests passed
in 578.603s (full311.log). Both ran against this exact source.

Two wheel builds under SOURCE_DATE_EPOCH=1788998400 are byte-identical
(wheel.log, wheel-report.json; wheel SHA256
641f557503619cf51d2decd63a12cfee063f5fc35f9748d484a1193193317786, 60 runtime
files). Installed-runtime smoke, format 2 to format 3 upgrade and rollback,
22 Guard tests, 22-module stdlib synchronization and all four contract-demo
outcomes passed.

Retained failures from the first integrated run are under record-fields-initial.
They are the regressions the focused tests missed: the response record's owned
field array changed the accounted charge during an HTTP write, was retained
until the VM's mark and sweep rather than immediately, and turned a budget
refusal into an internal error instead of overload. Those failures qualified
nothing; they are retained because they are what the full suite found.
