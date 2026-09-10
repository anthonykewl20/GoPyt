# Cipher destination-buffer qualification

Source is frozen in source.json. Full regression qualification is running.
The wheel reproduced byte-for-byte (SHA-256
37c62737209267ca9ef67aa3250ff1bf57e44761fde257e4063ef58104c6c11c,
57 runtime files, epoch 1788998400). Installed smoke and upgrade/rollback
checks passed; raw outputs are retained.

This increment admits plaintext and framed ciphertext buffers at the cipher
boundary and clears them on scope exit. SQLite serialization, restore backup
producer admission and native library allocator accounting remain incomplete.
