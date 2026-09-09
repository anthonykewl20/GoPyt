# Snapshot replay baseline

The unchanged runtime `a9c68e8f882d9928dedf039330832856fe4d74ea0f133ec0a80457ce73b4b7fa`
accepts an earlier valid encrypted snapshot after a newer write. The probe uses
fresh Store instances for both reads, strict security configuration and a private
random key outside the temporary application root. The key is never recorded.

`before.json` is Python 3.14.7; `before311.json` is Python 3.11.16. Both report a
balance of 9 before replay and 10 afterward. Ciphertext hashes differ because each
run uses independent keys and random nonces. The exact probe hash is retained in
each result. These are functional reproductions, not performance measurements.

The [proposed authority design](../../docs/storage-rollback-design.md) defines the
threat boundary and required implementation/failure tests. No rollback prevention
is implemented by this design-only commit.
