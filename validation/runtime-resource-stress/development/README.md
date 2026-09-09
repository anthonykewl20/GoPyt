# Development trials

These smoke runs are harness development, not sustained qualification. Protocol budgets were unchanged throughout. Inputs retain exact source hashes and dirty status. Available failed-source snapshots accompany their trial; hashes identify the remaining files.

- 01: cancellation handoffs were inspected after a successful recovery, which legitimately owns a result. Measure the cancellation boundary and release the recovery result.
- 02: the custom HTTP fixture returned a non-JSON-compatible union; use a bool result from the compiled database write. Also close the independent SQLite reader explicitly.
- 03: concurrent resource snapshots counted the sampler's temporary reader descriptor. Serialize snapshots.
- 04: the process-lock readiness condition incorrectly expected every caller to reach flock despite local serialization. Observe all lock callers and require a separately observed blocked process lock.
- 05: Python 3.14 smoke passed ten episodes, including both lock kinds.
- 311: Python 3.11 smoke passed ten episodes.

No trial here changed the runtime or weakened an acceptance budget. Measured runs are reported separately.
