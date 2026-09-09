"""Exact source identity for locks and artifacts, not release authentication."""
import hashlib
from pathlib import Path


def source_fingerprint(directory: Path) -> bytes:
    files = sorted((p for p in directory.glob('*.py') if not p.name.startswith('test_')),
                   key=lambda p: p.name.encode('utf-8'))
    if not files:
        raise RuntimeError('toolchain requires installed Python source files')
    digest = hashlib.sha256(b'GoPyT toolchain source identity v1\0')
    for path in files:
        name = path.name.encode('utf-8')
        digest.update(len(name).to_bytes(4, 'little'))
        digest.update(name)
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.digest()


# A running interpreter must not be hot-patched. Restart after toolchain changes.
FINGERPRINT = source_fingerprint(Path(__file__).parent)
TOOLCHAIN = 'gopyt-0.1.000+sha256:' + FINGERPRINT.hex()
