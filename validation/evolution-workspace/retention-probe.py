"""Measure retained proposal files across distinct valid package revisions."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from gopyt import evolve, toolchain
from gopyt.cli import cmd_fmt
from gopyt.manifest import package_digest
from gopyt.testing import write_lock


def main():
    source = Path('examples/auth').resolve()
    baseline = (source/'impl/auth.gopyt').read_text()
    source_hashes = {str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest()
                     for folder in ('spec','impl','test') for p in (source/folder).rglob('*') if p.is_file()}
    rows = []
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)/'auth'
        shutil.copytree(source, root, ignore=shutil.ignore_patterns('build', 'evolve', '.gopyt-state'))
        for iteration in range(5):
            ttl = 3600000 + iteration
            (root/'impl/auth.gopyt').write_text(baseline.replace('4, 3600000', f'4, {ttl}'))
            cmd_fmt(str(root))
            write_lock(str(root))
            before = package_digest(str(root))
            outcome = evolve.propose(str(root), True, 2, timeout_ms=10000)
            assert outcome.kind == 'Applied', outcome
            waves = sorted(p for p in (root/'evolve').iterdir() if p.is_dir())
            files = [p for wave in waves for p in wave.rglob('*') if p.is_file()]
            rows.append({'iteration':iteration, 'input_digest':before, 'output_digest':outcome.digest,
                         'retained_waves':len(waves), 'retained_files':len(files),
                         'retained_bytes':sum(p.stat().st_size for p in files)})
    print(json.dumps({'runtime_sha256':toolchain.FINGERPRINT.hex(),
                      'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      'baseline_sources':source_hashes, 'max_candidates':2, 'wave_timeout_ms':10000,
                      'trials':rows, 'scope':'isolated five-wave retention observation; not a disk quota or production stress qualification'},indent=2))


if __name__ == '__main__':
    main()
