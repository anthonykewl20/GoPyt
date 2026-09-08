# Immutable mapped-column research

`sealed_column.py` adapts the seal-before-sharing pattern from Ahmed S. Darwish's [memfd example, pinned commit](https://github.com/a-darwish/memfd-examples/blob/172d805b24e0c3283934ba009bfdadcc3ab92bf7/server.c). Leitir located and verified the source; its Unlicense was inspected. No upstream package is a runtime dependency. The local implementation also seals growth and validates the exact versioned little-endian i64 format.

The prototype copies a bounded input into a Linux memfd, applies write/grow/shrink/seal-policy seals, verifies them, and opens a read-only mapping. Tests attempt truncation, growth, direct writes and mapping mutation. The kernel rejects each. Data remains plaintext, and setup incurs a copy. This does not provide encrypted storage, a mutable IPC queue or crash durability.

Run `python -m prototypes.mapped_data.benchmark --out NEW.json`. It compares streaming, full-buffer, ordinary read-only mapping and sealed mapping in subprocesses over one synthetic 8 MiB column, three alternating rounds, checking sequential and random-query results against independent expected values. The ordinary mapped fixture is privately created and never mutated. RSS and page faults are recorded. No cache eviction, beyond-RAM stress, Parquet decode or GPU workload is measured.

The initial host measurement favored buffered access over mapping on this workload. It does not justify promoting a mapped native API yet.
