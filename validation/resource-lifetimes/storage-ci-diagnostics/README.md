# Storage CI diagnostic crashes

Both Linux Python 3.11 jobs on PR #71 head 28086d4 exited 139 during
scheduled native stack dumping. Raw job logs are retained. The precise
underlying cause remains unproven.

The launcher now uses a daemon Python thread to obtain live frame references
and print stacks every 120 seconds. Native faulthandler remains enabled only
for fatal signals. Test arguments, assertions and CI timeouts are unchanged.
This reporter needs the GIL and cannot diagnose a native call that holds it
indefinitely. Platform requalification is required.
