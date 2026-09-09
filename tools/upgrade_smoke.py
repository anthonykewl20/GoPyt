"""Qualify an explicit old/new wheel pair in one isolated install/rollback run."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import venv


PROBE = r'''
import json,sys
from pathlib import Path
import gopyt.gobyte as bytecode
from gopyt.cli import build
from gopyt.diag import CompileError
from gopyt.manifest import TOOLCHAIN
from gopyt.testing import write_pkg,write_lock
from gopyt.vm import VM,Trap
phase,base,environment=sys.argv[1:]
base=Path(base);app=base/'app'
assert Path(bytecode.__file__).resolve().is_relative_to(Path(environment))
files={
 'spec/demo.gopyt':'module demo\n\nfn answer(value: i64) -> i64\n    requires value >= 0\n    ensures result == value + 1\n',
 'impl/demo.gopyt':'module demo\n\nfn answer(value: i64) -> i64\n    requires value >= 0\n    ensures result == value + 1\n{\n    return value + 1\n}\n',
}
def rejected(call,code):
 try:call()
 except CompileError as exc:assert exc.diag.code==code
 else:raise AssertionError('required compatibility rejection missing')
if phase=='old':
 app.mkdir();write_pkg(str(app),files,fmt=True)
 (base/'old.lock').write_bytes((app/'gopyt.lock').read_bytes())
elif phase=='new':
 rejected(lambda:bytecode.decode((base/'old.gobyte').read_bytes()),100)
 before=(app/'gopyt.lock').read_bytes()
 rejected(lambda:build(str(app)),40)
 assert (app/'gopyt.lock').read_bytes()==before
 write_lock(str(app)) # Explicit operator acceptance, never a compiler side effect.
else:
 rejected(lambda:bytecode.decode((base/'new.gobyte').read_bytes()),100)
 (app/'gopyt.lock').write_bytes((base/'old.lock').read_bytes())
_,art,ids=build(str(app));data=bytecode.encode(art)
assert VM(art,str(app)).call(ids['demo.answer'],[41])==42
try:VM(art,str(app)).call(ids['demo.answer'],[-1])
except Trap as exc:assert exc.code==1
else:raise AssertionError('contract disappeared')
if phase=='rollback':assert data==(base/'old.gobyte').read_bytes()
else:(base/(phase+'.gobyte')).write_bytes(data)
print(json.dumps({'phase':phase,'format':bytecode.VERSION,'toolchain':TOOLCHAIN,
 'result':42,'requires_trap':1,'outside_checkout':True,
 'artifact_sha256':__import__('hashlib').sha256(data).hexdigest()}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('old_wheel', type=Path)
    parser.add_argument('new_wheel', type=Path)
    args = parser.parse_args()
    old, new = args.old_wheel.resolve(), args.new_wheel.resolve()
    report = {'old_wheel_sha256': hashlib.sha256(old.read_bytes()).hexdigest(),
              'new_wheel_sha256': hashlib.sha256(new.read_bytes()).hexdigest(),
              'phases': []}
    with tempfile.TemporaryDirectory(prefix='gopyt-upgrade-') as directory:
        root = Path(directory).resolve()
        environment = root / 'venv'
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / 'bin/python'
        env = {k: v for k, v in os.environ.items()
               if k != 'PYTHONPATH' and not k.startswith('GOPYT_')}
        def run(*command):
            result = subprocess.run(command, cwd=root, env=env, capture_output=True,
                                    text=True, timeout=90)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            return result.stdout
        for phase, wheel in [('old', old), ('new', new), ('rollback', old)]:
            run(str(python), '-m', 'pip', 'install', '--no-index', '--no-deps',
                '--force-reinstall', str(wheel))
            report['phases'].append(json.loads(run(str(python), '-I', '-c', PROBE,
                                                   phase, str(root), str(environment))))
    assert [p['format'] for p in report['phases']] == [2, 3, 2]
    assert report['phases'][0]['artifact_sha256'] == report['phases'][2]['artifact_sha256']
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
