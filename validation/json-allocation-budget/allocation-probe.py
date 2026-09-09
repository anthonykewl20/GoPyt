import json
import tempfile
from unittest.mock import patch
from gopyt.cli import build, make_vm
from gopyt.testing import write_pkg
from gopyt import ops, toolchain
from gopyt.vm import Trap

with tempfile.TemporaryDirectory() as root:
    write_pkg(root, {
        'spec/demo.gopyt': 'module demo\n\nuse core.status { ConvertError }\n\nfn render(value: str) -> str | ConvertError\n',
        'impl/demo.gopyt': 'module demo\n\nuse data.json { encode }\nuse core.status { ConvertError }\n\nfn render(value: str) -> str | ConvertError\n{\n    return data.json.encode(value)\n}\n',
    }, fmt=True)
    prog, art, ids = build(root)
    vm = make_vm(root, prog, art, ids)
    rows = []
    for value in ('x'*14, 'x'*15, '\x01'*3, 'é'*8):
        with patch.object(ops, 'MAX_ALLOC', 16):
            try:
                result = vm.call(ids['demo.render'], [value])
                actual = {'kind': type(result).__name__, 'encoded_bytes': len(result.encode('utf-8'))}
            except Trap as error:
                actual = {'kind': 'Trap', 'code': error.code}
        expected_bytes = len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
        rows.append({'input_bytes': len(value.encode('utf-8')), 'oracle_encoded_bytes': expected_bytes,
                     'expected': 'Trap14' if expected_bytes > 16 else 'str', 'actual': actual})
    print(json.dumps({'runtime_sha256': toolchain.FINGERPRINT.hex(), 'injected_allocation_limit': 16,
                      'scope': 'reduced-limit compiled native probe, not a 2 GiB allocation trial', 'trials': rows}, indent=2))
