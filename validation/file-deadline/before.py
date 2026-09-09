import hashlib,json,os,tempfile,time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from gopyt import files
from gopyt.testing import write_pkg
from gopyt.test_vm import module
from gopyt.cli import build
from gopyt.vm import VM,Trap
from gopyt.toolchain import FINGERPRINT,source_fingerprint

with tempfile.TemporaryDirectory() as temporary:
    root=os.path.realpath(temporary)
    signature='task persist(data: bytes) -> unit | IoError\n    effects { filesystem.write }\n'
    write_pkg(root,module(signature,signature+'{\n    return core.file.write("data.txt", data)\n}\n',
        uses='use core.status { IoError }\nuse core.file { write }',spec_uses='use core.status { IoError }'),fmt=True)
    _,art,ids=build(root)
    vm=VM(art,root)
    target=Path(root,'data.txt');target.write_bytes(b'before')
    original=files.parent_directory
    @contextmanager
    def delayed(*args,**kwargs):
        with original(*args,**kwargs) as opened:
            time.sleep(.3)
            yield opened
    start=time.monotonic_ns();vm.deadline_ns=start+100_000_000
    try:
        with patch('gopyt.files.parent_directory',delayed):
            vm.call(ids['demo.persist'],[b'after'])
        outcome='returned'
    except Trap as error:
        outcome={'trap':error.code}
    finally:
        elapsed=(time.monotonic_ns()-start)/1_000_000
        vm.deadline_ns=None
    report={'runtime_sha256':FINGERPRINT.hex(),'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'deadline_ms':100,'injected_directory_delay_ms':300,'elapsed_ms':elapsed,'outcome':outcome,
        'file_contents_after_timeout':target.read_text()}
assert source_fingerprint(Path('gopyt'))==FINGERPRINT
Path('/tmp/gopyt-file-deadline-before.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
