#!/usr/bin/env python3
"""Stream a SHA-pinned public UCI workbook into a replay corpus and SQL oracle.

Run explicitly with the downloaded XLSX; no runtime or test download occurs.
Customer identifiers, descriptions and prices are not needed for quantity replay.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import xml.etree.ElementTree as ET
import zipfile

XLSX_SHA256='43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d'
SOURCE='https://archive.ics.uci.edu/static/public/352/online+retail.zip'
NS='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def rows(path):
    with zipfile.ZipFile(path) as archive:
        strings=ET.fromstring(archive.read('xl/sharedStrings.xml'))
        shared=[''.join(si.itertext()) for si in strings]
        with archive.open('xl/worksheets/sheet1.xml') as sheet:
            parent=None
            for event,node in ET.iterparse(sheet,events=('start','end')):
                if event=='start' and node.tag==NS+'sheetData':parent=node
                if event!='end' or node.tag!=NS+'row':continue
                values={}
                for cell in node:
                    value=cell.find(NS+'v')
                    raw='' if value is None else value.text or ''
                    if cell.get('t')=='s':raw=shared[int(raw)]
                    column=''.join(ch for ch in cell.get('r','') if ch.isalpha())
                    values[column]=raw
                if node.get('r')!='1':yield int(node.get('r')),values
                parent.clear()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('xlsx',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with args.xlsx.open('rb') as source:
        digest=hashlib.file_digest(source,'sha256').hexdigest()
    if digest!=XLSX_SHA256:parser.error('source SHA256 differs; review a new dataset before accepting it')
    args.output.mkdir(parents=True,exist_ok=False)
    started=time.perf_counter();counts={'rows':0,'negative_quantity_rows':0,'zero_quantity_rows':0,'cancellation_rows':0}
    db=sqlite3.connect(args.output/'oracle.sqlite3')
    db.execute('CREATE TABLE source (row INTEGER PRIMARY KEY, invoice TEXT, sku TEXT, quantity INTEGER)')
    with (args.output/'rows.jsonl').open('w') as out:
        pending=[]
        for row,values in rows(args.xlsx):
            invoice,sku,quantity=values['A'],values['B'],int(values['D'])
            if not sku or not invoice:raise ValueError('missing source identity')
            record=[row,invoice,sku,quantity]
            out.write(json.dumps(record,ensure_ascii=True,separators=(',',':'))+'\n')
            pending.append(record);counts['rows']+=1
            counts['negative_quantity_rows']+=quantity<0;counts['zero_quantity_rows']+=quantity==0
            counts['cancellation_rows']+=invoice.startswith('C')
            if len(pending)==4096:
                db.executemany('INSERT INTO source VALUES (?,?,?,?)',pending);pending=[]
        db.executemany('INSERT INTO source VALUES (?,?,?,?)',pending);db.commit()
    if counts['rows']!=541909:raise AssertionError('source row count mismatch')
    oracle=dict(db.execute('SELECT sku,SUM(quantity) FROM source GROUP BY sku'))
    counts['unique_invoices']=db.execute('SELECT COUNT(DISTINCT invoice) FROM source').fetchone()[0]
    counts['unique_skus']=len(oracle);counts['quantity_sum']=sum(oracle.values());db.close()
    (args.output/'expected.json').write_text(json.dumps(oracle,sort_keys=True)+'\n')
    manifest={'source_url':SOURCE,'dataset_page':'https://archive.ics.uci.edu/dataset/352/online+retail',
              'citation':'Chen, D. (2015). Online Retail. UCI Machine Learning Repository. https://doi.org/10.24432/C5BW33',
              'license':'CC BY 4.0, as stated by UCI','xlsx_sha256':digest,'counts':counts,
              'preparation_seconds':time.perf_counter()-started,
              'transformations':['Retain every source row, including negative quantities, cancellations and repeated lines.',
                  'Project row number, invoice, stock code and integer quantity; omit customer IDs, descriptions, dates and prices.',
                  'Expected SKU totals computed independently with SQLite GROUP BY and SUM.',
                  'Replay batches are ingestion units; they do not assert atomicity of a complete original invoice.'],
              'sha256':{name:hashlib.sha256((args.output/name).read_bytes()).hexdigest() for name in ('rows.jsonl','expected.json','oracle.sqlite3')}}
    (args.output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps(manifest,indent=2))


if __name__=='__main__':main()
