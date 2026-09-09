#!/usr/bin/env python3
"""Replay the IERS Bulletin C 52 UTC markers without changing the host clock."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gopyt.cli import build
from gopyt.testing import write_pkg
from gopyt.toolchain import FINGERPRINT
from gopyt.vm import VM

SOURCE = 'https://datacenter.iers.org/data/16/bulletinc-052.txt'
SHA256 = 'e798dcb63dce37d4411f58a7d47e62f5fc170724eb4fb0dd039324ca2894cab0'
CASES = [('2016-12-31T23:59:59Z', 'core.time.Timestamp', [1483228799000000000]),
         ('2016-12-31T23:59:60Z', 'core.status.ConvertError', None),
         ('2017-01-01T00:00:00Z', 'core.time.Timestamp', [1483228800000000000])]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bulletin', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if hashlib.sha256(args.bulletin.read_bytes()).hexdigest() != SHA256:
        parser.error('bulletin differs from reviewed source')
    head = 'module probe\n\nuse core.time { Timestamp }\nuse core.status { ConvertError }\n\n'
    decl = 'fn parse(text: str) -> Timestamp | ConvertError\n'
    results = []
    with args.output.open('x') as out:
        with tempfile.TemporaryDirectory() as root:
            write_pkg(root, {'spec/probe.gopyt': head + decl,
                            'impl/probe.gopyt': head.replace('{ Timestamp }', '{ Timestamp, parse_timestamp }')
                            + decl + '{\n    return core.time.parse_timestamp(text)\n}\n'}, fmt=True)
            vm = VM(build(root)[1], root)
            for text, kind, fields in CASES:
                result = vm.call(vm.by_name['probe.parse'], [text])
                assert vm.type_name(result.type_id) == kind
                if fields is not None:
                    assert result.fields == fields
                results.append({'input': text, 'result_type': kind, 'fields': result.fields})
        report = {'status': 'passed', 'source_url': SOURCE, 'source_sha256': SHA256,
                  'source_title': 'IERS Bulletin C 52, 6 July 2016',
                  'runtime_sha256': FINGERPRINT.hex(),
                  'tool_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'transformation': 'Three announced UTC markers transcribed to explicit-Z inputs. No host-clock adjustment.',
                  'expected_policy': 'POSIX timestamp domain rejects the real leap-second marker explicitly.',
                  'cases': results}
        json.dump(report, out, indent=2)
        out.write('\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
