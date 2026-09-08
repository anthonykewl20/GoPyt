"""Independent raw-workbook scalar extraction for guard calibration (stdlib only)."""
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

NS='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'

def sha(raw):return hashlib.sha256(raw).hexdigest()

def prepare(archive, provenance, out):
    out.mkdir(parents=True,exist_ok=False)
    metadata=json.loads(provenance.read_bytes())
    raw=archive.read_bytes();assert sha(raw)==metadata['archive_sha256']
    with zipfile.ZipFile(io.BytesIO(raw)) as z:book=z.read('Online Retail.xlsx')
    assert sha(book)==metadata['workbook_sha256']
    counts=Counter();pairs={};invoices={'development':set(),'evaluation':set()}
    with zipfile.ZipFile(io.BytesIO(book)) as z:
        strings=[''.join(s.itertext()) for s in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        with z.open('xl/worksheets/sheet1.xml') as stream:
            for _,row in ET.iterparse(stream,events=['end']):
                if row.tag!=NS+'row':continue
                number=int(row.attrib['r'])
                if number==1:row.clear();continue
                counts['source_rows']+=1
                values={}
                for cell in row:
                    v=cell.find(NS+'v')
                    if v is not None:
                        value=v.text
                        if cell.attrib.get('t')=='s':value=strings[int(value)]
                        col=''.join(c for c in cell.attrib['r'] if c.isalpha())
                        values[col]=value
                try:
                    q=Decimal(values.get('D','NaN'));p=Decimal(values.get('F','NaN'))*100
                    if not q.is_finite() or not p.is_finite():raise ValueError('nonfinite')
                    if q!=q.to_integral_value():raise ValueError('noninteger_quantity')
                    if p!=p.to_integral_value():
                        if abs(p-p.to_integral_value())<=Decimal('0.000000001'):
                            counts['serialization_residue_normalized']+=1;p=p.to_integral_value()
                        else:raise ValueError('subpenny_price')
                    if not -1000000<=q<=1000000 or not -100000000<=p<=100000000:
                        raise ValueError('outside_supported_range')
                    invoice=values.get('A','')
                    if not invoice:raise ValueError('missing_invoice')
                    split='development' if hashlib.sha256(('guard-calibration-v1:'+invoice).encode()).digest()[0]<205 else 'evaluation'
                    invoices[split].add(invoice)
                    key=(int(q),int(p));expected=int(q*p)
                    if key not in pairs:
                        pairs[key]={'args':list(key),'expected':expected,'development':0,'evaluation':0,'first_row':number,'source_rows':{}}
                    record=pairs[key];assert record['expected']==expected
                    record[split]+=1
                    record['source_rows'].setdefault(split,{'row':number,'invoice':invoice,'quantity':values['D'],'unit_price_sterling':values['F']})
                    counts['included_rows']+=1;counts[split+'_rows']+=1
                    if q<0:counts['negative_quantity_rows']+=1
                    if q==0:counts['zero_quantity_rows']+=1
                    if p<0:counts['negative_price_rows']+=1
                    if p==0:counts['zero_price_rows']+=1
                except (ValueError,InvalidOperation) as exc:
                    counts['excluded_'+str(exc)]+=1
                row.clear()
    assert counts['source_rows']==541909
    assert counts['included_rows']+sum(v for k,v in counts.items() if k.startswith('excluded_'))==counts['source_rows']
    assert not invoices['development'] & invoices['evaluation']
    records=sorted(pairs.values(),key=lambda r:r['first_row'])
    payload=json.dumps(records,sort_keys=True,separators=(',',':')).encode()+b'\n'
    (out/'pairs.json').write_bytes(payload)
    result={'source':metadata['source'],'citation':metadata['citation'],'license':metadata['license'],
            'archive_sha256':sha(raw),'workbook_sha256':sha(book),'pairs_sha256':sha(payload),
            'extractor_sha256':sha(Path(__file__).read_bytes()),'counts':dict(counts),'unique_pairs':len(records),
            'invoice_counts':{k:len(v) for k,v in invoices.items()},'invoice_overlap':0,
            'pair_overlap':sum(bool(r['development'] and r['evaluation']) for r in records),
            'note':'Invoice-disjoint, not numeric-input-disjoint or historically unseen data. All rows retained as weighted counts; one representative source location per pair per split.'}
    (out/'provenance.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--archive',type=Path,required=True);p.add_argument('--provenance',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();prepare(a.archive,a.provenance,a.out)
