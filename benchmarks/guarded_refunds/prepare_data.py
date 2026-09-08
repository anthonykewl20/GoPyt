"""Normalize genuine UCI invoice inputs without inferring payment/refund history.

Stdlib XLSX reader for the pinned workbook, not a general spreadsheet API.
No external code/dependencies are fetched or executed.
"""
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare():
    archive = ROOT/'data/online-retail.zip'
    with zipfile.ZipFile(archive) as z:
        workbook = z.read('Online Retail.xlsx')
    with zipfile.ZipFile(io.BytesIO(workbook)) as z:
        strings = [''.join(item.itertext()) for item in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        counts = Counter()
        invoices = {}
        with z.open('xl/worksheets/sheet1.xml') as sheet:
            for _, row in ET.iterparse(sheet, events=['end']):
                if row.tag != NS+'row':
                    continue
                if row.attrib['r'] == '1':
                    row.clear()
                    continue
                counts['rows'] += 1
                values = {}
                for cell in row:
                    v = cell.find(NS+'v')
                    if v is not None:
                        text = v.text
                        if cell.attrib.get('t') == 's':
                            text = strings[int(text)]
                        column = ''.join(c for c in cell.attrib['r'] if c.isalpha())
                        values[column] = text
                invoice = values.get('A', '')
                reason = None
                if not invoice.isdigit():
                    reason = 'cancellation_or_nonstandard_invoice'
                if reason:
                    counts[reason] += 1
                    row.clear()
                    continue
                customer = values.get('G', '')
                item = invoices.setdefault(invoice, {'id': invoice, 'customer': customer,
                                                     'paid': 0, 'lines': 0, 'invalid': False})
                if not customer.isdigit():
                    reason = 'missing_or_nonstandard_customer'
                elif customer != item['customer']:
                    reason = 'inconsistent_customer'
                try:
                    quantity = Decimal(values.get('D', 'NaN'))
                    pence = Decimal(values.get('F', 'NaN')) * 100
                    if pence.is_finite() and pence != pence.to_integral_value() and abs(pence - pence.to_integral_value()) <= Decimal('0.000000001'):
                        counts['excel_serialization_residue_normalized'] += 1
                        pence = pence.to_integral_value()
                    if not quantity.is_finite() or quantity <= 0 or quantity != quantity.to_integral_value():
                        reason = reason or 'nonpositive_or_invalid_quantity'
                    elif not pence.is_finite() or pence <= 0 or pence != pence.to_integral_value():
                        reason = reason or 'nonpositive_or_subpenny_price'
                    elif pence * quantity > 10**12:
                        reason = reason or 'line_above_supported_limit'
                    else:
                        item['paid'] += int(pence * quantity)
                except InvalidOperation:
                    reason = reason or 'invalid_numeric'
                item['lines'] += 1
                if reason:
                    counts[reason] += 1
                    item['invalid'] = True
                row.clear()
    eligible = []
    for item in invoices.values():
        if item['invalid'] or not 0 < item['paid'] <= 10**12:
            counts['excluded_invoices'] += 1
        else:
            eligible.append({k:item[k] for k in ('id','customer','paid','lines')})
    eligible.sort(key=lambda x: x['id'])
    counts['eligible_invoices'] = len(eligible)
    counts['eligible_lines'] = sum(r['lines'] for r in eligible)
    counts['numeric_invoices'] = len(invoices)
    if counts['rows'] != 541909:
        raise ValueError('unexpected source row count')
    output = ROOT/'data/invoices.json'
    output.write_text(json.dumps(eligible, sort_keys=True, separators=(',',':'))+'\n')
    provenance = {
        'schema': 'gopyt.refund-data.v1',
        'source': 'https://archive.ics.uci.edu/dataset/352/online+retail',
        'download': 'https://archive.ics.uci.edu/static/public/352/online%2Bretail.zip',
        'citation': 'Chen, D. (2015). Online Retail. UCI Machine Learning Repository. doi:10.24432/C5BW33',
        'license': 'CC BY 4.0', 'license_url': 'https://creativecommons.org/licenses/by/4.0/',
        'archive_sha256': sha(archive), 'workbook_sha256': hashlib.sha256(workbook).hexdigest(),
        'normalized_sha256': sha(output), 'normalizer_sha256': sha(Path(__file__)),
        'counts': dict(counts),
        'adaptation': 'Group complete eligible positive invoices; Decimal price*100 must be integral within 1e-9 pence; normalize only that Excel serialization residue. Larger subpenny values are refused. No deduplication or cancellation matching. Exclude whole invoices with any invalid line.',
        'limits': ['Observed quantity and price totals are not evidence of settled payment.',
                   'The test seeds these totals as simulated paid balances; refund operations are synthetic.',
                   'Customer identifiers are dataset identifiers, not authenticated principals.',
                   'Negative/cancellation rows are counted, not interpreted as matched refunds.']}
    (ROOT/'data/provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    print(json.dumps(provenance,indent=2))


if __name__ == '__main__':
    prepare()
