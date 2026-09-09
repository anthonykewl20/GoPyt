# Packaging input and umask qualification

The reproduction tool previously omitted setup.py, setup.cfg and MANIFEST.in.
A fixture with dynamic metadata supplied by setup.py fails under the old snapshot
and succeeds with the hook included. `hook-before-after.json` retains both results,
including the initial inspection's incorrect assumption that the old build would
complete. The regression test checks the emitted Summary and source hash.

Including the project's setup.py also exposed caller-umask-dependent ZIP file
permissions. `umask-difference.json` records identical payload bytes but different
archive permissions; fixing the subprocess umask to 0022 reproduces the earlier
CI wheel exactly at the same epoch. `reproduction.json` includes the setup.py hash
and explicit umask. The regression builds a real hook fixture under caller umasks
0002 and 0077 and requires identical wheel hashes.

`source.json` freezes the runtime/tests/tool/packaging inputs before full regression.
`focused.log` and `full.log` retain validation. Runtime source and bytecode format
are unchanged. Cross-platform qualification of this tool revision remains pending.

Final validation: 768 tests passed in 270.801 seconds; all frozen hashes matched after completion. Six focused wheel tests passed on both Python 3.14.7 and 3.11.16.
