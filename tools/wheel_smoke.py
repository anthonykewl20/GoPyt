"""Install a wheel in a fresh environment and run outside the source checkout."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import venv


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('wheel', type=Path); args=parser.parse_args()
    wheel=args.wheel.resolve(); source=Path(__file__).resolve().parents[1]
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
        print(json.dumps({'wheel':wheel.name,'outside_checkout':True,'compiler':True,'lsp':True,'guard_entrypoint':True}))


if __name__=='__main__':main()
