"""Bounded synthetic i64 comparison; include sealed-copy setup in its timing."""
import argparse
import json
import mmap
import os
from pathlib import Path
import random
import resource
import struct
import subprocess
import sys
import tempfile
import time
from prototypes.mapped_data.sealed_column import HEADER, MAGIC, VALUE, validate, sealed_column


def worker(path, mode):
    started = time.perf_counter()
    before = resource.getrusage(resource.RUSAGE_SELF)
    def measure(buffer):
        count = validate(buffer)
        total = sum(value[0] for value in struct.iter_unpack('<q', memoryview(buffer)[HEADER.size:]))
        rng = random.Random(42)
        random_total = sum(VALUE.unpack_from(buffer, HEADER.size + rng.randrange(count)*8)[0] for _ in range(10000))
        return total, random_total
    if mode == 'buffered': result = measure(path.read_bytes())
    elif mode == 'mapped':
        with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            result = measure(data)
    elif mode == 'sealed':
        with sealed_column(path.read_bytes()) as (_, data): result = measure(data)
    else:
        with path.open('rb') as stream:
            magic, count = HEADER.unpack(stream.read(HEADER.size))
            if magic != MAGIC or path.stat().st_size != HEADER.size + count*8: raise ValueError('column')
            total = 0
            while block := stream.read(65536): total += sum(v[0] for v in struct.iter_unpack('<q', block))
            rng = random.Random(42); random_total = 0
            for _ in range(10000):
                stream.seek(HEADER.size + rng.randrange(count)*8)
                random_total += VALUE.unpack(stream.read(8))[0]
            result = total, random_total
    after = resource.getrusage(resource.RUSAGE_SELF)
    return dict(mode=mode, seconds=time.perf_counter()-started, sum=result[0], random_sum=result[1],
                peak_rss_platform_units=after.ru_maxrss, minor_faults=after.ru_minflt-before.ru_minflt,
                major_faults=after.ru_majflt-before.ru_majflt)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--out', type=Path)
    parser.add_argument('--worker', choices=['streaming','buffered','mapped','sealed']); parser.add_argument('--path', type=Path)
    args = parser.parse_args()
    if args.worker: print(json.dumps(worker(args.path,args.worker))); return
    if not args.out: parser.error('--out required')
    runs = []
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp)/'column.bin'; count = 1024*1024
        with path.open('wb') as stream:
            stream.write(HEADER.pack(MAGIC,count))
            for offset in range(0,count,8192): stream.write(b''.join(VALUE.pack(i%2001-1000) for i in range(offset,offset+8192)))
        expected = sum(i%2001-1000 for i in range(count))
        rng = random.Random(42); random_expected = sum(rng.randrange(count)%2001-1000 for _ in range(10000))
        for repeat in range(3):
            modes = ['streaming','buffered','mapped','sealed']
            if repeat%2: modes.reverse()
            for mode in modes:
                result = subprocess.run([sys.executable,'-m','prototypes.mapped_data.benchmark','--worker',mode,'--path',str(path)],capture_output=True,text=True,timeout=60,check=True)
                item=json.loads(result.stdout); assert (item['sum'],item['random_sum']) == (expected,random_expected)
                item['repeat']=repeat; runs.append(item)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open('x') as stream:
        json.dump(dict(bytes=HEADER.size+count*8,rows=count,runs=runs,
          limits='Synthetic 8 MiB column, warm cache possible, local host. No beyond-RAM, encrypted mmap, GPU, or IPC throughput claim. RSS units: KiB on Linux, bytes on macOS.'),stream,indent=2)
    print(json.dumps({'runs':len(runs),'all_checks_match':True}))


if __name__ == '__main__': main()
