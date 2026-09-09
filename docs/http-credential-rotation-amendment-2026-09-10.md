# Live HTTP service-token rotation

A listener configured with `GOPYT_HTTP_TOKEN_FILE` pins that file path and its
service-token authentication mode at startup. It reads the private file at every
request authentication gate, including keep-alive requests. Removing/changing an
environment variable does not disable or retarget an already running listener.
Listeners initially configured without service-token authentication do not acquire
that mode through later environment changes. Verified-session authentication is a
separate mode and retains its existing revocation semantics.

Operators rotate a token by writing the new value to a private file in the same
protected directory, flushing/fsyncing it, atomically replacing the configured file,
and fsyncing the directory. Preserve private single-link regular-file permissions,
operator ownership and the path outside the application package. The token must
contain 32..256 printable non-space ASCII bytes, subject to the existing 256-byte
file-read bound; use OS randomness rather than passwords. In-place editing is not
the supported rotation protocol. Provision the new credential to authorized clients
through their protected channel before changing server admission policy.

A request reads one file snapshot at its gate. Requests that open the replacement
accept the new token and reject the old one. A request admitted against the previous
snapshot may finish under its existing deadline; rotation does not cancel effects
already admitted. This is an admission boundary, not a claim of instantaneous
revocation across in-flight work or simultaneous distributed listeners.

Missing, malformed, linked, nonprivate or inaccessible credential files produce
empty 503 and close the request without handler dispatch. Invalid or absent supplied
credentials produce empty 401 with the existing Bearer challenge. Restoring a valid
file allows subsequent requests to recover without restarting the listener. File
contents and supplied credentials are not emitted in these responses or telemetry.
Operators must keep tokens out of application logs and restore reasons themselves.

Credential reads are synchronous host filesystem operations, with the request's
cancellation/deadline checked before and after successful reading. A blocked OS call
can exceed the requested budget; expiry prevents subsequent handler admission.
This does not introduce a secret broker, hardware custody, multi-token grace window
or forced cancellation of host calls. Backup/key retention, plaintext migration and
storage-writer rotation remain distinct issue #11 work.

The [pre-implementation design](http-credential-rotation-design.md) and retained
baseline establish the original stale-listener behavior. Concurrent workload and
regression results are recorded under `validation/http-credential-rotation/` as they
complete; finite load trials do not establish production SLOs or universal security.
