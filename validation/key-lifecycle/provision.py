"""Execute the published provisioning snippet, including no-overwrite rejection."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
doc = ROOT/'docs/key-lifecycle.md'
code = doc.read_text().split('```python\n',1)[1].split('```',1)[0]
result = {'doc_sha256':hashlib.sha256(doc.read_bytes()).hexdigest(),
          'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          'python':sys.version, 'cases':[]}
with tempfile.TemporaryDirectory() as temporary:
    directory = Path(temporary).resolve()
    for kind, length in [('storage',32),('http',64)]:
        env = dict(os.environ, DIRECTORY=str(directory), KIND=kind, NAME=kind)
        first = subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,timeout=10)
        assert first.returncode == 0 and first.stdout == b'' and first.stderr == b''
        path = directory/kind; value = path.read_bytes()
        assert len(value) == length and path.stat().st_mode & 0o777 == 0o600
        if kind == 'http': assert all(c in b'0123456789abcdef' for c in value)
        second = subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,timeout=10)
        assert second.returncode != 0 and b'FileExistsError' in second.stderr
        assert path.read_bytes() == value and second.stdout == b''
        assert value not in first.stdout+first.stderr+second.stdout+second.stderr
        result['cases'].append({'kind':kind,'length':length,'private_mode':True,'no_overwrite':True,'no_secret_output':True})
result['status']='passed'
Path(sys.argv[1]).write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
