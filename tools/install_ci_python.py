"""Install the reviewed CI-only CPython archive after exact hash verification."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import urllib.request


def install(pin, destination):
    destination.mkdir(parents=True, exist_ok=False)
    archive = destination / 'python.tar.gz'
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(pin['url'], timeout=30) as response, archive.open('wb') as stream:
        while block := response.read(1024 * 1024):
            size += len(block)
            if size > pin['size']:
                raise ValueError('Python archive exceeds reviewed size')
            digest.update(block)
            stream.write(block)
    if size != pin['size'] or digest.hexdigest() != pin['sha256']:
        raise ValueError('Python archive does not match reviewed identity')
    with tarfile.open(archive) as bundle:
        # Current bootstrap is pinned Python 3.14; data filtering rejects unsafe
        # paths, links and special files even in a hash-approved archive.
        bundle.extractall(destination, filter='data')
    python = destination / 'python/bin/python3'
    result = subprocess.run([str(python), '-I', '-c',
        'import json,sys;print(json.dumps(list(sys.version_info[:3])))'],
        check=True, capture_output=True, text=True, timeout=30)
    if '.'.join(map(str, json.loads(result.stdout))) != pin['version']:
        raise ValueError('Python interpreter version mismatch')
    # Actions steps invoke python; create a link only if the archive omitted it.
    command = python.with_name('python')
    if not command.exists():
        command.symlink_to('python3')
    return command.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version', choices=('3.11.16', '3.14.7'), required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    pins = json.loads((root / 'requirements/python-standalone.json').read_text())
    key = args.version + '/' + platform.system() + '/' + platform.machine()
    pin = pins[key]
    binary = install(pin, args.output.resolve())
    if 'GITHUB_PATH' in os.environ:
        with open(os.environ['GITHUB_PATH'], 'a') as stream:
            stream.write(str(binary) + '\n')
    print(json.dumps({'platform': key, **pin, 'bin': str(binary)}))


if __name__ == '__main__':
    main()
