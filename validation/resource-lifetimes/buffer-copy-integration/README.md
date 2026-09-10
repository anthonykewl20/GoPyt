Buffer copy integration evidence

Source identity is frozen in source.json. Reproducible wheel, installed smoke,
upgrade/rollback, 17 guard tests, 22-module stdlib sync and contract demonstration
passed. Wheel SHA256 d9d9d5fa72d7e3767434ddd9675fa793342f4809790cfdfda562e59b298b043d.
Python 3.14 full language suite remains running; Python 3.11 and final deadline
inventory qualification remain pending. This is not complete issue #5 evidence.

Full Python 3.14 FAILED: 1104 tests, 374.363s, six failures (four compiled-resource
zero-reservation assertions plus two diagnostic audits). Copied bytes remain in
the VM tracing heap; review result-root release and collection on idle teardown.
The full failed log is retained. Packaging does not qualify this failed runtime.
