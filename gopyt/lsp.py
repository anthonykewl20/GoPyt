"""Bounded stdio language server for dependency-free GoPyT packages."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import tomllib
from urllib.parse import urlparse,unquote
from gopyt.diag import CompileError
from gopyt.parser import parse_module
from gopyt.fmt import fmt_module

MAX_BYTES=2_000_000


def file_path(uri):
    parsed=urlparse(uri)
    if parsed.scheme!='file' or parsed.netloc not in ('','localhost') or parsed.query or parsed.fragment:
        raise ValueError('only local file URIs supported')
    return Path(unquote(parsed.path)).resolve()


def span(line=0,end=None):
    return {'start':{'line':line,'character':0},'end':{'line':line if end is None else end,'character':0}}



def _checked_project(temp):
    """Check an editor snapshot, inside an OS profile where one is available.

    Editor input is not operator-reviewed, so the checker runs under the same
    boundary as Guard candidates when the platform supports one. Where no
    profile can be installed the existing bounded subprocess still applies, and
    the editor keeps working rather than losing checking entirely.
    """
    from gopyt import isolation
    command = [sys.executable, '-I', '-c',
               'import sys;sys.path.insert(0, sys.argv[1]);'
               'import runpy;sys.argv=["gopyt.lsp_check", sys.argv[2]];'
               'runpy.run_module("gopyt.lsp_check", run_name="__main__")',
               str(Path(__file__).resolve().parent.parent), temp]
    usable, _ = isolation.available()
    if not usable:
        return subprocess.run([sys.executable, '-m', 'gopyt.lsp_check', temp],
                              capture_output=True, text=True, timeout=8)
    checked, _how = isolation.run(command, writable=[temp], timeout=8, required=True)
    return checked


class Workspace:
    def __init__(self,root):
        self.root=Path(root).resolve();self.documents={};self.cache={}
    def relative(self,uri):
        path=file_path(uri);rel=path.relative_to(self.root)
        if len(rel.parts)<2 or rel.parts[0] not in ('spec','impl','test') or rel.suffix!='.gopyt':
            raise ValueError('document must be inside spec/, impl/ or test/')
        return rel
    def update(self,uri,text,version):
        self.relative(uri)
        if type(text) is not str or len(text.encode('utf-8'))>MAX_BYTES or type(version) is not int:
            raise ValueError('invalid or oversized document')
        previous=self.documents.get(uri)
        if previous and version<=previous[0]:return False
        if sum(len(value[1].encode('utf-8')) for key,value in self.documents.items() if key!=uri)+len(text.encode('utf-8'))>MAX_BYTES:
            raise ValueError('overlay byte budget exceeded')
        self.documents[uri]=(version,text);self.cache.pop(uri,None);return True
    def source(self,uri):
        rel=self.relative(uri)
        return self.documents[uri][1] if uri in self.documents else (self.root/rel).read_text()
    def module(self,uri):
        source=self.source(uri);cached=self.cache.get(uri)
        if cached and cached[0]==source:return cached[1]
        rel=self.relative(uri);module=parse_module(source,rel.as_posix(),rel.parts[0])
        self.cache[uri]=(source,module);return module
    def symbols(self,uri):
        module=self.module(uri);result=[]
        for item in module.items:
            declaration=getattr(item,'sig',item)
            name=getattr(declaration,'name',None)
            if name:
                pos=span(max(0,declaration.line-1))
                result.append({'name':name,'kind':12 if hasattr(declaration,'params') else 23,'range':pos,'selectionRange':pos})
        return result
    def diagnostics(self):
        results={uri:[] for uri in self.documents}
        try:
            paths=[self.root/'gopyt.toml']
            for role in ('spec','impl','test'):
                paths.extend((self.root/role).rglob('*.gopyt'))
            if len(paths)>256:raise ValueError('project file limit exceeded')
            files={};total=0
            for path in paths:
                rel=path.relative_to(self.root)
                if path.is_symlink() or not path.resolve().is_relative_to(self.root):raise ValueError('source symlink outside project')
                with path.open('rb') as stream:raw=stream.read(MAX_BYTES+1)
                total+=len(raw)
                if total>MAX_BYTES:raise ValueError('project byte limit exceeded')
                files[rel]=raw
            manifest=tomllib.loads(files[Path('gopyt.toml')].decode())
            if 'deps' in manifest:raise ValueError('initial LSP supports dependency-free packages')
            for uri,(_,source) in self.documents.items():files[self.relative(uri)]=source.encode()
            if sum(map(len,files.values()))>MAX_BYTES:raise ValueError('combined project byte limit exceeded')
            with tempfile.TemporaryDirectory(prefix='gopyt-lsp-') as temp:
                for rel,raw in files.items():
                    path=Path(temp)/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
                try:
                    checked=_checked_project(temp)
                except subprocess.TimeoutExpired:
                    raise ValueError('project checking exceeded editor time limit') from None
                if checked.returncode or len(checked.stdout)>MAX_BYTES:
                    raise ValueError('project checker exceeded resources or failed')
                detail=json.loads(checked.stdout)
                if 'error' in detail:raise ValueError(detail['error'])
                if detail:
                    uri=(self.root/(detail['file'] or 'gopyt.toml')).as_uri()
                    results.setdefault(uri,[]).append({'range':span(max(0,(detail['line'] or 1)-1)),
                        'severity':1,'code':f"GOPYT_E{detail['code']:03d}",
                        'source':'gopyt','message':detail['message']})
        except CompileError as exc:
            diag=exc.diag;uri=(self.root/(diag.file or 'gopyt.toml')).as_uri()
            results.setdefault(uri,[]).append({'range':span(max(0,(diag.line or 1)-1)),
                'severity':1,'code':f'GOPYT_E{diag.code:03d}','source':'gopyt','message':str(exc)})
        except (OSError,ValueError,KeyError) as exc:
            for uri in results:
                results[uri]=[{'range':span(),'severity':1,'code':'LSP_PROJECT','source':'gopyt','message':str(exc)}]
        return results


class Server:
    def __init__(self):self.workspace=None;self.shutdown=False;self.published=set()
    def handle(self,message):
        method=message['method'];params=message.get('params',{});result=None;notifications=[]
        if method=='initialize':
            self.workspace=Workspace(file_path(params['rootUri']))
            result={'capabilities':{'positionEncoding':'utf-16','textDocumentSync':1,
                      'documentFormattingProvider':True,'documentSymbolProvider':True,'completionProvider':{}},
                    'serverInfo':{'name':'gopyt-lsp','version':'0.1'}}
        elif method=='shutdown':self.shutdown=True
        elif method=='exit':return [],True
        elif method=='initialized':pass
        elif self.workspace is None or self.shutdown:raise ValueError('server not initialized or already shut down')
        elif method in ('textDocument/didOpen','textDocument/didChange'):
            doc=params['textDocument'];uri=doc['uri']
            if method.endswith('didOpen'):text=doc['text']
            else:
                changes=params['contentChanges']
                if len(changes)!=1 or 'range' in changes[0]:raise ValueError('full document changes required')
                text=changes[0]['text']
            if self.workspace.update(uri,text,doc['version']):
                diags=self.workspace.diagnostics()
                for path in self.published-set(diags):diags[path]=[]
                self.published=set(diags)
                for path,items in diags.items():
                    payload={'uri':path,'diagnostics':items}
                    if path in self.workspace.documents:payload['version']=self.workspace.documents[path][0]
                    notifications.append({'jsonrpc':'2.0','method':'textDocument/publishDiagnostics','params':payload})
        elif method=='textDocument/didClose':
            uri=params['textDocument']['uri'];self.workspace.documents.pop(uri,None);self.workspace.cache.pop(uri,None)
            diags=self.workspace.diagnostics()
            for path in self.published-set(diags):diags[path]=[]
            diags.setdefault(uri,[])
            self.published=set(diags)
            for path,items in diags.items():
                notifications.append({'jsonrpc':'2.0','method':'textDocument/publishDiagnostics','params':{'uri':path,'diagnostics':items}})
        elif method=='textDocument/documentSymbol':result=self.workspace.symbols(params['textDocument']['uri'])
        elif method=='textDocument/completion':
            result=[{'label':item['name'],'kind':3 if item['kind']==12 else 22} for item in self.workspace.symbols(params['textDocument']['uri'])]
        elif method=='textDocument/formatting':
            uri=params['textDocument']['uri'];source=self.workspace.source(uri)
            lines=source.split('\n');end={'line':len(lines)-1,'character':len(lines[-1].encode('utf-16-le'))//2}
            result=[{'range':{'start':{'line':0,'character':0},'end':end},'newText':fmt_module(self.workspace.module(uri))}]
        else:
            if 'id' in message:return [{'jsonrpc':'2.0','id':message['id'],'error':{'code':-32601,'message':'method not supported'}}],False
        if 'id' in message:notifications.insert(0,{'jsonrpc':'2.0','id':message['id'],'result':result})
        return notifications,False


def read_message(stream):
    headers={};total=0
    while True:
        line=stream.readline(8193)
        if not line:return None
        total+=len(line)
        if total>8192:raise ValueError('header too large')
        if line==b'\r\n':break
        key,sep,value=line.partition(b':')
        key=key.strip().lower()
        if not sep or key in headers:raise ValueError('invalid or duplicate header')
        headers[key]=value.strip()
    size=int(headers[b'content-length'])
    if not 0<size<=MAX_BYTES:raise ValueError('message byte limit exceeded')
    raw=stream.read(size)
    if len(raw)!=size:raise ValueError('truncated message')
    value=json.loads(raw)
    if type(value) is not dict or value.get('jsonrpc')!='2.0' or type(value.get('method')) is not str:
        raise ValueError('expected JSON-RPC request object')
    return value


def main():
    server=Server()
    while True:
        try:message=read_message(sys.stdin.buffer)
        except (ValueError,KeyError,UnicodeError,RecursionError) as exc:
            print(str(exc),file=sys.stderr);return 1
        if message is None:return 0
        try:responses,stop=server.handle(message)
        except (ValueError,KeyError,TypeError,CompileError,OSError,RecursionError) as exc:
            responses=([{'jsonrpc':'2.0','id':message['id'],'error':{'code':-32602,'message':str(exc)}}] if 'id' in message else [])
            stop=False
        for response in responses:
            raw=json.dumps(response,ensure_ascii=False).encode()
            sys.stdout.buffer.write(f'Content-Length: {len(raw)}\r\n\r\n'.encode()+raw);sys.stdout.buffer.flush()
        if stop:return 0


if __name__=='__main__':raise SystemExit(main())
