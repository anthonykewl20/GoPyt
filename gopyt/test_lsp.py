import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from gopyt.lsp import Workspace, Server, read_message, MAX_BYTES
from gopyt.testing import write_pkg

SPEC = 'module sample\n\nfn add(value: i64) -> i64\n'
IMPL = 'module sample\n\nfn add(value: i64) -> i64\n{\n    return value + 1\n}\n'


def wire(message):
    raw = json.dumps(message).encode()
    return f'Content-Length: {len(raw)}\r\n\r\n'.encode() + raw


class LanguageServer(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        write_pkg(str(self.root), {'spec/sample.gopyt': SPEC, 'impl/sample.gopyt': IMPL})
        self.uri = (self.root / 'impl/sample.gopyt').as_uri()

    def test_unsaved_diagnostics_revert_and_stale_versions(self):
        ws = Workspace(self.root)
        self.assertTrue(ws.update(self.uri, IMPL, 1))
        self.assertEqual(ws.diagnostics(), {self.uri: []})
        self.assertEqual(ws.symbols(self.uri)[0]['name'], 'add')
        broken = IMPL.replace('return value + 1', 'return "wrong"')
        ws.update(self.uri, broken, 2)
        self.assertTrue(ws.diagnostics()[self.uri])
        self.assertFalse(ws.update(self.uri, IMPL, 1))
        self.assertEqual(ws.source(self.uri), broken)
        self.assertEqual((self.root / 'impl/sample.gopyt').read_text(), IMPL)
        server = Server(); server.workspace = ws; server.published = {self.uri}
        replies, _ = server.handle({'method': 'textDocument/didClose', 'params': {'textDocument': {'uri': self.uri}}})
        self.assertTrue(all(not item['params']['diagnostics'] for item in replies))

    def test_path_and_message_boundaries(self):
        ws = Workspace(self.root)
        for uri in ('https://example.test/a.gopyt', (self.root.parent / 'impl/a.gopyt').as_uri()):
            with self.assertRaises(ValueError): ws.update(uri, IMPL, 1)
        with self.assertRaises(ValueError): ws.update(self.uri, 'a' * (MAX_BYTES + 1), 1)
        for payload in (b'Content-Length: 1\r\nContent-Length: 1\r\n\r\n0',
                        b'Content-Length: 2000001\r\n\r\n',
                        b'Content-Length: 50\r\n\r\n{}'):
            with self.assertRaises(ValueError): read_message(io.BytesIO(payload))

    def test_actual_stdio_initialize_symbols_shutdown(self):
        messages = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'rootUri': self.root.as_uri()}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'textDocument/documentSymbol', 'params': {'textDocument': {'uri': self.uri}}},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'shutdown'},
            {'jsonrpc': '2.0', 'method': 'exit'}]
        result = subprocess.run([sys.executable, '-m', 'gopyt.lsp'], input=b''.join(map(wire, messages)), capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        stream = io.BytesIO(result.stdout); replies = []
        while line := stream.readline():
            size = int(line.split(b':')[1]); self.assertEqual(stream.readline(), b'\r\n')
            replies.append(json.loads(stream.read(size)))
        self.assertEqual(replies[0]['result']['capabilities']['positionEncoding'], 'utf-16')
        self.assertEqual(replies[1]['result'][0]['name'], 'add')
        self.assertIsNone(replies[2]['result'])
