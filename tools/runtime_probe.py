#!/usr/bin/env python3
"""Repeat the closure audit's real VM and HTTP load experiments."""
import argparse
import concurrent.futures
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gopyt.cli import build
from gopyt.values import Record
from gopyt.vm import VM
from gopyt.test_vm import HttpFromStr


def auth_load():
    with tempfile.TemporaryDirectory() as temp:
        root = str(Path(temp, 'auth'))
        shutil.copytree(ROOT / 'examples/auth', root)
        _, art, ids = build(root)
        vm = VM(art, root)
        cells = vm.observe.memory_cells()
        start = time.monotonic()
        samples = []
        for index in range(100_000):
            vm.call(ids['auth.login'], [Record(vm.type_id_of('auth.LoginReq'),
                    ['user' + str(index), 'invalid'])])
            if (index + 1) % 10_000 == 0:
                vm.heap.release_result()
                vm.heap.collect()
                sample = {'calls': index + 1, 'limiter_buckets': len(vm.buckets),
                          'observe_cells': vm.observe.memory_cells(),
                          'heap_live': len(vm.heap.objects), 'collections': vm.heap.collections}
                assert sample['limiter_buckets'] <= 4096
                assert sample['observe_cells'] == cells
                assert sample['heap_live'] < 300
                samples.append(sample)
        return {'calls': 100_000, 'seconds': round(time.monotonic() - start, 3),
                'samples': samples, 'alarm': vm.observe.cusum.alarm}


def http_load():
    fixture = HttpFromStr('test_a_convertible_placeholder_reaches_the_handler')
    fixture.setUp()
    try:
        def request(index):
            with urllib.request.urlopen(f'http://127.0.0.1:{fixture.port}/user/{index}', timeout=10) as response:
                value = json.loads(response.read())
                assert response.status == 200 and value == {'value': index}, value
        start = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(request, range(2000)))
        fixture.vm.heap.collect()
        return {'requests': 2000, 'clients': 16, 'seconds': round(time.monotonic() - start, 3),
                'heap_live': len(fixture.vm.heap.objects), 'collections': fixture.vm.heap.collections,
                'observe_cells': fixture.vm.observe.memory_cells()}
    finally:
        fixture.tearDown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name, experiment in [('auth-load', auth_load), ('http-load', http_load)]:
        result = experiment()
        (args.output / (name + '.json')).write_text(json.dumps(result, indent=2) + '\n')
        print(name + ': passed')


if __name__ == '__main__':
    main()
