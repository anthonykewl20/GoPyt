# Live service-token rotation design

Issue #11 includes credential rotation under load. The frozen baseline probe on
runtime `b612d392140aaf04a9f93edd1e110cdc61cee08d563a806ea3dc094ab3202cba`
shows a running listener continuing to accept the old service token and rejecting
the new one after atomic replacement of its token file. Before and after status
pairs are both `[200, 401]` for old/new tokens. No token bytes are retained.

The listener will pin its configured authentication mode and token-file path at
startup. In service-token mode, every request, including keep-alive requests,
will read that private file at its authentication gate. Atomic operator replacement
will select the credential for subsequent admissions without restarting the server.
Missing, malformed or inaccessible credential files will return empty 503 and
close the request without calling the handler. Wrong/missing credentials return
401. Changing/removing an environment variable will not disable a listener's
already configured protection. Verified-session authentication remains separate.

Requests admitted against a previously opened token-file snapshot may finish under
their existing request context. Rotation does not retroactively cancel effects or
promise a revocation instant across already admitted work. Operators must replace
files atomically; in-place edits are not the supported update procedure. The same
private-file, external-to-package and printable bounded-token checks apply at
startup and reload. Credential read failures will not expose file contents or
credentials in responses, exception logs or telemetry.

Functional acceptance before implementation: old token accepted/new rejected before
replacement; new accepted/old rejected afterward on one listener and keep-alive
connections; missing/malformed/private-file violations fail closed then recover;
startup path/mode remains pinned; admitted requests retain their documented
semantics. A frozen concurrent workload will retain authorization outcomes against
an independent expected-status oracle, failed trials and actual scope before any
load qualification claim. This does not close the separate plaintext migration,
stale storage writer, backup custody or production key-management requirements.
