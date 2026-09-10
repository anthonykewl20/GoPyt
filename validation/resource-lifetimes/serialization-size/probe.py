import json,sqlite3,sys,tempfile
from pathlib import Path
results=[]
for page_size in (512,4096,65536):
    db=sqlite3.connect(':memory:')
    db.execute(f'PRAGMA page_size={page_size}')
    db.execute('CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID')
    for phase in ('empty','insert','delete','vacuum','deserialize'):
        if phase=='insert':
            db.executemany('INSERT INTO kv VALUES (?,?)',[(str(i),'x'*(i*31)) for i in range(80)])
        elif phase=='delete':db.execute('DELETE FROM kv WHERE CAST(key AS INTEGER)%2=0')
        elif phase=='vacuum':db.commit();db.execute('VACUUM')
        elif phase=='deserialize':
            image=db.serialize();db.close();db=sqlite3.connect(':memory:');db.deserialize(image)
        db.commit()
        pages=db.execute('PRAGMA main.page_count').fetchone()[0]
        size=db.execute('PRAGMA main.page_size').fetchone()[0]
        image=db.serialize()
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'snapshot.db';disk=sqlite3.connect(path)
            db.backup(disk);disk.close()
            disk_size=path.stat().st_size
        assert len(image)==pages*size==disk_size,(phase,page_size,pages,size,len(image),disk_size)
        results.append(dict(phase=phase,page_size=size,pages=pages,image_bytes=len(image),backup_file_bytes=disk_size))
    db.close()
print(json.dumps(dict(python=sys.version,sqlite=sqlite3.sqlite_version,cases=results,scope='15 finite image-size cases; no allocator or concurrency bound'),indent=2))
