"""Reconcile retained architecture evidence without changing its results."""
import argparse
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def workbook_projection(path):
    """Independent extraction implementation; does not import retail_prepare."""
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    with zipfile.ZipFile(path) as archive:
        strings = ET.fromstring(archive.read('xl/sharedStrings.xml'))
        text = [''.join(t.text or '' for t in si.iter(ns + 't')) for si in strings]
        with archive.open('xl/worksheets/sheet1.xml') as source:
            row_number = None; cells = {}; data = None
            for event, node in ET.iterparse(source, events=('start', 'end')):
                if event == 'start':
                    if node.tag == ns + 'sheetData': data = node
                    if node.tag == ns + 'row': row_number = int(node.attrib['r']); cells = {}
                elif node.tag == ns + 'c':
                    column = node.attrib['r'].rstrip('0123456789')
                    if column in ('A', 'B', 'D'):
                        value = node.findtext(ns + 'v', '')
                        cells[column] = text[int(value)] if node.get('t') == 's' else value
                    node.clear()
                elif node.tag == ns + 'row':
                    if row_number != 1:
                        yield [row_number, cells['A'], cells['B'], int(cells['D'])]
                    data.clear()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--xlsx', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    evidence = ROOT / 'validation/architecture'
    frozen = json.loads((evidence / 'validated-sources.json').read_text())['sha256']
    source_differences = [name for name, digest in frozen.items() if sha(ROOT/name) != digest]
    assert not source_differences, source_differences
    manifest = json.loads((args.data / 'manifest.json').read_text())
    assert sha(args.xlsx) == manifest['xlsx_sha256']
    for name, digest in manifest['sha256'].items(): assert sha(args.data/name) == digest
    counts = Counter(); quantities = Counter(); invoices = set()
    with sqlite3.connect(f'file:{args.data / "oracle.sqlite3"}?mode=ro', uri=True) as db:
        sql_rows = db.execute('SELECT row,invoice,sku,quantity FROM source ORDER BY row')
        with (args.data / 'rows.jsonl').open() as source:
            projected = map(json.loads, source)
            for index, (row, sql, original) in enumerate(itertools.zip_longest(
                    projected, sql_rows, workbook_projection(args.xlsx)), start=2):
                assert row is not None and sql is not None and original is not None
                assert row == list(sql) == original, index
                assert row[0] == index
                _, invoice, sku, quantity = row
                counts['rows'] += 1; counts['negative_quantity_rows'] += quantity < 0
                counts['zero_quantity_rows'] += quantity == 0
                counts['cancellation_rows'] += invoice.startswith('C')
                invoices.add(invoice); quantities[sku] += quantity
        sql_totals = dict(db.execute('SELECT sku,SUM(quantity) FROM source GROUP BY sku'))
    assert dict(quantities) == sql_totals == json.loads((args.data/'expected.json').read_text())
    counts.update(unique_invoices=len(invoices), unique_skus=len(quantities), quantity_sum=sum(quantities.values()))
    assert dict(counts) == manifest['counts']
    trials = []
    for path in sorted(evidence.glob('retail-*/report.json')):
        report = json.loads(path.read_text()); result = report['result']; assert report['complete']
        workers = [json.loads(p.read_text()) for p in sorted(path.parent.glob('worker-*.json'))]
        for result_name, worker_name in [('applied_batches', 'applied_batches'),
                ('duplicate_batches', 'duplicate_batches'), ('conflicts', 'conflicts'),
                ('source_rows', 'source_rows_applied')]:
            assert result[result_name] == sum(w[worker_name] for w in workers)
        latency = sorted(t for w in workers for t in w['latency_ms'])
        assert len(latency) == result['applied_batches'] + result['duplicate_batches']
        assert statistics.median(latency) == result['p50_batch_ms']
        for pct in (95, 99):
            assert latency[min(len(latency)-1, int(len(latency)*pct/100))] == result[f'p{pct}_batch_ms']
        assert result['max_worker_peak_rss_bytes'] == max(w['peak_rss_bytes'] for w in workers)
        assert abs(result['source_rows_per_second'] - result['source_rows']/result['wall_seconds_including_worker_startup_and_replay']) < 1e-9
        limit = report['configuration']['limit_rows']
        if limit:
            with sqlite3.connect(f'file:{args.data / "oracle.sqlite3"}?mode=ro', uri=True) as db:
                expected = dict(db.execute('SELECT sku,SUM(quantity) FROM source WHERE row<=? GROUP BY sku', (limit+1,)))
        else: expected = sql_totals
        assert (result['sku_totals_verified'], result['quantity_sum']) == (len(expected), sum(expected.values()))
        differences = [name for name, digest in report['source_sha256'].items() if sha(ROOT/name) != digest]
        trials.append({'report': str(path.relative_to(ROOT)), 'worker_reports': len(workers),
                       'latency_samples': len(latency), 'arithmetic_reconciled': True,
                       'source_differences_from_audited_tree': differences})
    report = {'complete': True, 'source_commit': subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'python': sys.version, 'validated_source_files': len(frozen), 'source_differences': source_differences,
              'workbook_sha256': sha(args.xlsx), 'prepared_hashes': manifest['sha256'],
              'independent_workbook_rows_equal_corpus_and_sql': True, 'counts': dict(counts),
              'trials': trials,
              'limits': ['Reconciliation checks retained measurements; it does not re-run their trials.',
                         'A separate full HTTP replay is retained under validation/checklist-review.',
                         'Workload and oracle share historical source data; no customer workload or SLO is inferred.']}
    with args.output.open('x') as out: json.dump(report, out, indent=2); out.write('\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
