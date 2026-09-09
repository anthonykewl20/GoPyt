#!/usr/bin/env python3
"""Replay every hash-pinned retail date through compiled nanosecond time APIs."""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gopyt.cli import build
from gopyt.testing import write_pkg
from gopyt.toolchain import FINGERPRINT, source_fingerprint
from gopyt.values import Record
from gopyt.vm import VM
from retail_prepare import XLSX_SHA256, SOURCE, rows

SPEC = '''module time_replay

use core.time { Timestamp, Duration }
use core.status { ConvertError }

fn parse(text: str) -> Timestamp | ConvertError

fn delta(later: Timestamp, earlier: Timestamp) -> Duration | ConvertError

fn restore(earlier: Timestamp, delta: Duration) -> Timestamp | ConvertError
'''
IMPL = '''module time_replay

use core.time { Timestamp, Duration, parse_timestamp, difference, add }
use core.status { ConvertError }

fn parse(text: str) -> Timestamp | ConvertError
{
    return core.time.parse_timestamp(text)
}

fn delta(later: Timestamp, earlier: Timestamp) -> Duration | ConvertError
{
    return core.time.difference(later, earlier)
}

fn restore(earlier: Timestamp, delta: Duration) -> Timestamp | ConvertError
{
    return core.time.add(earlier, delta)
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('xlsx', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    digest = hashlib.sha256(args.xlsx.read_bytes()).hexdigest()
    if digest != XLSX_SHA256:
        parser.error('workbook differs from reviewed source')
    tool_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'status': 'running', 'source_sha256': digest, 'source_url': SOURCE,
              'citation': 'Chen, D. (2015). Online Retail. UCI. doi:10.24432/C5BW33. CC BY 4.0.',
              'runtime_sha256': FINGERPRINT.hex(), 'tool_sha256': tool_digest, 'python': sys.version,
              'acceptance': '541909 rows, zero parse/difference/restore mismatches',
              'policy': 'Source has naive Excel 1900-system date cells. Interpret as UTC only for this test; subtract serial 25569 for the Unix epoch, preserve the exact rational cell value and explicitly round to nearest nanosecond ties-even. This does not establish historical timezone or clock-fault causation.',
              'counts': {'rows': 0, 'adjacent_backwards': 0, 'adjacent_equal': 0,
                         'fractional_nanosecond_rounding': 0, 'not_exact_milliseconds': 0},
              'backwards_examples': []}
    started = time.monotonic()
    with args.output.open('x') as out:
        try:
            with tempfile.TemporaryDirectory() as root:
                write_pkg(root, {'spec/time_replay.gopyt': SPEC, 'impl/time_replay.gopyt': IMPL}, fmt=True)
                vm = VM(build(root)[1], root)
                cache, retained = {}, []
                retained.append(cache)
                retained.append(None)
                previous_ns = None
                minimum = maximum = None
                def call(name, args, kind, expected):
                    result = vm.call(vm.by_name['time_replay.' + name], args)
                    if (not isinstance(result, Record) or vm.type_name(result.type_id) != 'core.time.' + kind
                            or result.fields != [expected]):
                        raise AssertionError((name, expected, result))
                    return result
                with vm.heap.pin(retained):
                    for row, cells in rows(args.xlsx):
                        raw = cells['E']
                        serial = Fraction(raw)
                        if serial < 61:
                            raise ValueError('pre-March-1900 Excel date requires separate policy')
                        exact_ns = (serial - 25569) * 86_400_000_000_000
                        ns = round(exact_ns)
                        if raw not in cache:
                            seconds, nanos = divmod(ns, 1_000_000_000)
                            text = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(seconds)) + f'.{nanos:09d}Z'
                            cache[raw] = call('parse', [text], 'Timestamp', ns)
                        current = cache[raw]
                        if previous_ns is not None:
                            delta = ns - previous_ns
                            difference = call('delta', [current, retained[1]], 'Duration', delta)
                            call('restore', [retained[1], difference], 'Timestamp', ns)
                            report['counts']['adjacent_backwards'] += delta < 0
                            report['counts']['adjacent_equal'] += delta == 0
                            if delta < 0 and len(report['backwards_examples']) < 8:
                                report['backwards_examples'].append({'row': row, 'previous_ns': previous_ns,
                                                                      'current_ns': ns, 'difference_ns': delta})
                        retained[1] = current
                        previous_ns = ns
                        minimum = ns if minimum is None else min(minimum, ns)
                        maximum = ns if maximum is None else max(maximum, ns)
                        report['counts']['rows'] += 1
                        report['counts']['fractional_nanosecond_rounding'] += exact_ns.denominator != 1
                        report['counts']['not_exact_milliseconds'] += ns % 1_000_000 != 0
                assert report['counts']['rows'] == 541909
                report.update(unique_date_cells=len(cache), minimum_ns=minimum, maximum_ns=maximum, mismatches=0)
            assert source_fingerprint(ROOT/'gopyt') == FINGERPRINT
            assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == tool_digest
            assert hashlib.sha256(args.xlsx.read_bytes()).hexdigest() == digest
            report['status'] = 'passed'
        except BaseException as error:
            report['status'] = 'failed'
            report['error'] = type(error).__name__ + ': ' + str(error)
            raise
        finally:
            report['seconds'] = time.monotonic() - started
            json.dump(report, out, indent=2)
            out.write('\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
