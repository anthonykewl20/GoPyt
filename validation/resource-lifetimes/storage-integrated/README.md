# Integrated storage input and finalizer qualification

Source baseline: 34d479c; runtime eefa0fb167f4f3bdcf65a5f0b9750c00efeb11fdfb393833d1b6b3c47298dc61.

The combined runtime passed 45 focused tests. The wheel reproduced byte-for-byte
(SHA-256 e94bad2a1fff7d80f9f659520bdea36697563ab657dbe30e035d20a13acf3890,
57 runtime files, epoch 1788998400). Installed smoke and old/new/rollback checks
passed. Full regression qualification is pending.

The earlier storage Python 3.11 run was interrupted on the pre-fix budget lock;
its log remains in ../storage-payload. This increment accounts for snapshot
input reads, not SQLite, plaintext or cryptographic internal allocations.
