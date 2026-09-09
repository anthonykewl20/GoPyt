"""Install a wheel in a fresh environment and run outside the source checkout."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import venv
import zipfile


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('wheel', type=Path); args=parser.parse_args()
    wheel=args.wheel.resolve(); source=Path(__file__).resolve().parents[1]
    with zipfile.ZipFile(wheel) as archive:
        if any(name.startswith('gopyt/test_') for name in archive.namelist()):
            raise AssertionError('development test modules shipped in runtime wheel')
    with tempfile.TemporaryDirectory(prefix='gopyt-wheel-') as directory:
        root=Path(directory).resolve(); environment=root/'venv'
        venv.EnvBuilder(with_pip=True).create(environment)
        python=environment/'bin/python'; env=dict(os.environ)
        env.pop('PYTHONPATH',None)
        for key in tuple(env):
            if key.startswith('GOPYT_'):env.pop(key)
        def run(*command,cwd=root,**kwargs):
            result=subprocess.run(command,cwd=cwd,env=env,capture_output=True,timeout=90,**kwargs)
            if result.returncode:raise RuntimeError(result.stderr.decode())
            return result
        run(str(python),'-m','pip','install','--no-deps',str(wheel))
        app=root/'inventory';shutil.copytree(source/'examples/inventory',app,ignore=shutil.ignore_patterns('build','.gopyt-state','.gopyt-transaction.lock'))
        run(str(environment/'bin/gopyt'),'check',cwd=app)
        request={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'rootUri':app.as_uri()}}
        raw=json.dumps(request).encode();message=f'Content-Length: {len(raw)}\r\n\r\n'.encode()+raw
        reply=run(str(environment/'bin/gopyt-lsp'),input=message).stdout
        if b'capabilities' not in reply:raise AssertionError(reply)
        result=run(str(python),'-m','gopyt.lsp_check',str(app))
        if json.loads(result.stdout)!={}:raise AssertionError(result.stdout)
        run(str(environment/'bin/gopyt-guard'),'--help')
        run(str(environment/'bin/gopyt-store'),'--help')
        retail=root/'retail';shutil.copytree(source/'examples/retail_replay',retail,ignore=shutil.ignore_patterns('build','.gopyt-state','.gopyt-transaction.lock'))
        run(str(environment/'bin/gopyt'),'check',cwd=retail)
        run(str(python),'-c', '''from gopyt.cli import build
from gopyt.vm import VM
from gopyt.values import Record,Some,NONE
_,art,ids=build('.')
vm=VM(art,'.')
change=vm.type_id_of('store.db.Change')
assert vm.call(ids['retail.commit'],[[Record(change,['a',NONE,Some('1')]),Record(change,['b',NONE,Some('2')])]]) is True
out=vm.call(ids['retail.read'],[['a','b']])
assert [v.value for v in out.fields[0]]==['1','2']
''',cwd=retail)
        print(json.dumps({'wheel':wheel.name,'outside_checkout':True,'compiler':True,'lsp':True,'guard_entrypoint':True,
                          'store_admin_entrypoint':True,'compiled_transactions':True,'test_modules_excluded':True}))


if __name__=='__main__':main()
