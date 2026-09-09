"""Compare two wheels from frozen packaging inputs and verify their contents."""
import argparse
import base64
import csv
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import zipfile


def inspect_wheel(path: Path, expected: dict[str, bytes]) -> dict:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) > 1024 or sum(i.file_size for i in archive.infolist()) > sum(map(len, expected.values())) + 1048576:
            raise ValueError('wheel inventory exceeds verification budget')
        if len(names) != len(set(names)):
            raise ValueError('duplicate wheel entries')
        records = [n for n in names if n.endswith('.dist-info/RECORD')]
        if len(records) != 1:
            raise ValueError('wheel requires one RECORD')
        record = records[0]
        rows = list(csv.reader(io.StringIO(archive.read(record).decode())))
        if any(len(row) != 3 for row in rows):
            raise ValueError('invalid RECORD row')
        if len(rows) != len(names) or {row[0] for row in rows} != set(names):
            raise ValueError('RECORD inventory mismatch')
        metadata = record.rsplit('/', 1)[0] + '/'
        if not {metadata + 'METADATA', metadata + 'WHEEL'} <= set(names):
            raise ValueError('missing wheel metadata')
        for name in names:
            if name in expected:
                if archive.read(name) != expected[name]:
                    raise ValueError('wheel differs from source: ' + name)
            elif name not in {metadata + leaf for leaf in ('METADATA', 'WHEEL', 'RECORD', 'entry_points.txt', 'top_level.txt')} and not name.startswith(metadata + 'licenses/'):
                raise ValueError('unexpected wheel payload: ' + name)
            if name.startswith('/') or '..' in name.split('/'):
                raise ValueError('unsafe wheel entry')
        if not set(expected) <= set(names):
            raise ValueError('missing runtime source')
        hashes = {}
        for name, digest, size in rows:
            data = archive.read(name)
            if name == record:
                if digest or size:
                    raise ValueError('RECORD must not hash itself')
            else:
                want = 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
                if digest != want or size != str(len(data)):
                    raise ValueError('wheel RECORD hash/size mismatch: ' + name)
            hashes[name] = hashlib.sha256(data).hexdigest()
    return {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'files': hashes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--epoch', type=int, required=True)
    args = parser.parse_args()
    if not 315532800 <= args.epoch <= 4354819198:
        raise ValueError('epoch outside ZIP timestamp range')
    versions = {n: importlib.metadata.version(n) for n in ('pip', 'setuptools')}
    if versions != {'pip': '26.2.1', 'setuptools': '82.0.1'}:
        raise ValueError('install the pinned requirements/build.txt before building')
    source = args.source.resolve()
    paths = [source / 'pyproject.toml', source / 'README.md']
    paths += [source / name for name in ('setup.py', 'setup.cfg', 'MANIFEST.in')
              if (source / name).exists()]
    paths += sorted(p for p in (source / 'gopyt').glob('*.py') if not p.name.startswith('test_'))
    paths += sorted({p for pattern in ('LICENSE*', 'NOTICE*') for p in source.glob(pattern) if p.is_file()})
    if any(p.is_symlink() for p in paths):
        raise ValueError('packaging input symlink')
    frozen = {str(p.relative_to(source)): p.read_bytes() for p in paths}
    expected = {p: data for p, data in frozen.items() if p.startswith('gopyt/')}
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'epoch': args.epoch, 'python': sys.version, 'platform': platform.platform(),
              'runner': {k: os.environ.get(k) for k in ('ImageOS', 'ImageVersion', 'RUNNER_ARCH')},
              'build_versions': versions, 'build_umask': '0022',
              'source_sha256': {p: hashlib.sha256(data).hexdigest() for p, data in frozen.items()},
              'builds': []}
    with tempfile.TemporaryDirectory(prefix='gopyt-reproduce-') as directory:
        base = Path(directory)
        for trial in range(2):
            staging = base / ('first' if trial == 0 else 'different-parent/second')
            staging.mkdir(parents=True)
            for name, data in frozen.items():
                path = staging / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(0o644)
                os.utime(path, (args.epoch + trial * 86400, args.epoch + trial * 86400))
            destination = output / str(trial)
            destination.mkdir()
            env = dict(os.environ, SOURCE_DATE_EPOCH=str(args.epoch), PYTHONHASHSEED=str(trial + 1))
            result = subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps',
                '--no-build-isolation', '--no-cache-dir', '--no-index', str(staging),
                '--wheel-dir', str(destination)], env=env, umask=0o022, capture_output=True, text=True, timeout=120)
            (destination / 'build.log').write_text(result.stdout + result.stderr)
            if result.returncode:
                raise RuntimeError('wheel build failed; see ' + str(destination / 'build.log'))
            wheels = list(destination.glob('*.whl'))
            if len(wheels) != 1:
                raise ValueError('expected exactly one wheel')
            report['builds'].append(inspect_wheel(wheels[0], expected))
    report['bitwise_equal'] = report['builds'][0]['sha256'] == report['builds'][1]['sha256']
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    if not report['bitwise_equal']:
        raise ValueError('wheel builds differ; both artifacts and manifests retained')
    print(json.dumps({'bitwise_equal': True, 'wheel_sha256': report['builds'][0]['sha256'],
                      'runtime_files': len(expected)}))


if __name__ == '__main__':
    main()
