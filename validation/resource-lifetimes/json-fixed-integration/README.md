Corrected JSON integration evidence

Frozen source/runtime identity is recorded in source.json. Two wheel builds are
byte-identical (SHA256 5ce5e416a66f55836874de97eaa25da98e507c11ba695f57758170e6737cc4ed).
Installed smoke, upgrade/rollback, 17 guard tests, 22-module stdlib synchronization
and all four contract-demo outcomes passed. Python 3.14 full-suite is still running;
Python 3.11 full-suite and deadline inventory remain pending. Prior failed full-suite
evidence remains under json-integration and does not qualify this corrected source.

Corrected Python 3.14.7 full language suite: 1096 tests passed in 366.871s.
Frozen Python source hashes match. Python 3.11 full-suite remains pending.
