#!/usr/bin/env python3
"""Qualify compiled fixed-point money against exact UCI workbook decimal cells."""
import argparse
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
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

SPEC = '''module money_replay

use core.money { Money }
use core.status { ConvertError }

fn price(text: str) -> Money | ConvertError

fn line(price: Money, quantity: i64) -> Money | ConvertError

fn total(left: Money, right: Money) -> Money | ConvertError
'''
IMPL = '''module money_replay

use core.money { Money, Rounding, parse, multiply_ratio, add }
use core.status { ConvertError }

fn price(text: str) -> Money | ConvertError
{
    return core.money.parse(text, 2, "GBP", Rounding.HalfEven)
}

fn line(price: Money, quantity: i64) -> Money | ConvertError
{
    return core.money.multiply_ratio(price, quantity, 1, Rounding.Exact)
}

fn total(left: Money, right: Money) -> Money | ConvertError
{
    return core.money.add(left, right)
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('xlsx', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    digest = hashlib.sha256(args.xlsx.read_bytes()).hexdigest()
    if digest != XLSX_SHA256:
        parser.error('workbook differs from the reviewed source')
    tool_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'status': 'running', 'source_sha256': digest, 'source_url': SOURCE,
              'citation': 'Chen, D. (2015). Online Retail. UCI. doi:10.24432/C5BW33. CC BY 4.0.',
              'runtime_sha256': FINGERPRINT.hex(), 'tool_sha256': tool_digest,
              'python': sys.version, 'acceptance': 'all 541909 rows; zero line/aggregate mismatches',
              'policy': 'Preserve exact workbook decimal cells, expanding exponent syntax only; round unit price once to GBP scale 2 HalfEven, then multiply by signed quantity exactly. This is a declared test policy, not evidence of amounts charged.',
              'counts': {'rows': 0, 'negative_quantity': 0, 'negative_price': 0,
                         'zero_price': 0, 'rounded_unit_price': 0, 'rounding_order_difference': 0},
              'examples': []}
    started = time.monotonic()
    with args.output.open('x') as out:
        try:
            with tempfile.TemporaryDirectory() as root, localcontext() as context:
                context.prec = 100
                write_pkg(root, {'spec/money_replay.gopyt': SPEC, 'impl/money_replay.gopyt': IMPL}, fmt=True)
                vm = VM(build(root)[1], root)
                money_id = vm.type_id_of('core.money.Money')
                aggregate = Record(money_id, [0, 2, 'GBP'])
                expected_total = 0
                price_cache = {}
                def call(name, *values):
                    result = vm.call(vm.by_name['money_replay.' + name], list(values))
                    if not isinstance(result, Record) or result.type_id != money_id:
                        raise AssertionError(('unexpected result', name, result))
                    return result
                retained = [price_cache, aggregate]
                with vm.heap.pin(retained):
                    for row, cells in rows(args.xlsx):
                        quantity, raw = int(cells['D']), cells['F']
                        price = Decimal(raw)
                        expected_price = int((price * 100).to_integral_value(rounding=ROUND_HALF_EVEN))
                        if raw not in price_cache:
                            # Decimal expands lexical exponent notation exactly, without quantization.
                            price_cache[raw] = call('price', format(price, 'f'))
                            assert price_cache[raw].fields == [expected_price, 2, 'GBP'], row
                        actual = call('line', price_cache[raw], quantity)
                        expected_line = expected_price * quantity
                        assert actual.fields == [expected_line, 2, 'GBP'], row
                        aggregate = call('total', aggregate, actual)
                        retained[1] = aggregate
                        expected_total += expected_line
                        assert aggregate.fields == [expected_total, 2, 'GBP'], row
                        rounded = price * 100 != Decimal(expected_price)
                        alternate = int((price * quantity * 100).to_integral_value(rounding=ROUND_HALF_EVEN))
                        counts = report['counts']
                        counts['rows'] += 1
                        counts['negative_quantity'] += quantity < 0
                        counts['negative_price'] += price < 0
                        counts['zero_price'] += price == 0
                        counts['rounded_unit_price'] += rounded
                        counts['rounding_order_difference'] += alternate != expected_line
                        if alternate != expected_line and len(report['examples']) < 8:
                            report['examples'].append({'row': row, 'raw_price': raw, 'quantity': quantity,
                                                       'unit_first_units': expected_line, 'line_first_units': alternate})
                assert report['counts']['rows'] == 541909
                report.update(total_units=expected_total, unique_price_cells=len(price_cache), mismatches=0)
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
