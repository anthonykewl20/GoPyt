# Resource budget parser evidence

Runtime `faea6f96806f0d65399a0cbc1700d4d84651d1be5f946a1d2d9efc81c7291c99`,
reviewed runtime commit `fb4fda0ef02389d70c2e7b5fa896ae5aea59c66c`.
The [coverage review](../../docs/runtime-resource-budget-review.md) records scope
and unresolved conversion/evolution budgets. This evidence does not close A2–A4.

Run from the repository root:

```sh
PYTHONPATH=. python validation/resource-budget-review/parser_probe.py
```

`python311.json` and `python314.json` retain all 12 literal expected/actual
outcomes on pinned Python 3.11.16 and 3.14.7, interpreter/platform identity,
probe and actual imported HTTP/runtime source hashes. Each run passed seven
in-memory parser boundaries and five real compiled GoPyt server wire trials.
The four rejected wire requests never invoked the handler; the subsequent valid
request invoked it exactly once and returned 200. The fixture supplies server
startup and teardown; expected statuses and payload lengths are literal protocol
oracles independent of handler code. Shutdown joined the fixture server.

Header count includes the terminating blank line, while trailer count excludes
it. Source limits are not RSS measurements: parsed objects, copies, socket
buffers and the OS backlog are separate. These short probes establish boundary
rejection and recovery, not sustained fairness, latency, leaks or all networking
behavior. Existing deadline tests qualify the transport budgets separately.

`parser_probe_initial.py` and `initial311.log` retain the failed first harness:
`object.__new__(HTTPResponse)` works on 3.14 but is rejected on 3.11. The final
probe uses the public HTTPResponse constructor with an in-memory makefile
provider. No runtime change was made to fix this harness error. Both versions
were rerun using the same corrected probe bytes. No source from the reference
cache was copied or executed; probes use the installed job standard library.
