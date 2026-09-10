# Cipher destination-buffer qualification

Source is frozen in source.json. Full regression qualification is running.
The wheel reproduced byte-for-byte (SHA-256
37c62737209267ca9ef67aa3250ff1bf57e44761fde257e4063ef58104c6c11c,
57 runtime files, epoch 1788998400). Installed smoke and upgrade/rollback
checks passed; raw outputs are retained.

This increment admits plaintext and framed ciphertext buffers at the cipher
boundary and clears them on scope exit. SQLite serialization, restore backup
producer admission and native library allocator accounting remain incomplete.

Guard calibration passed 17 tests, stdlib parity passed for 22 modules and
the contract demo passed. Raw outputs are retained.

Full Python 3.14.7 qualification passed 1029 tests in 364.632 seconds.
Frozen source and lock hashes were verified unchanged. Full Python 3.11
qualification remains pending.
