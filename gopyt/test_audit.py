"""Independent regression probes from the September 2026 implementation audit."""

import struct
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from pathlib import Path

from gopyt import gobyte, jsonc, ops
from gopyt.check import load_package
from gopyt.diag import CompileError
from gopyt.testing import diag_code, run_cli, write_pkg
from gopyt.test_vm import module


class SourceAudit(unittest.TestCase):
    def test_direct_polymorphic_recursion_is_e029_even_when_unused(self):
        with tempfile.TemporaryDirectory() as root:
            header = "fn grow[T](value: T) -> unit\n"
            write_pkg(root, module(header, header + "{\n    return grow(some(value))\n}\n"), fmt=True)
            self.assertEqual(diag_code(root), 29)

    def test_mutual_polymorphic_recursion_is_e029(self):
        with tempfile.TemporaryDirectory() as root:
            declarations = "fn grow[T](value: T) -> unit\n\nfn again[T](value: T) -> unit\n\nfn start() -> unit\n"
            bodies = ("fn grow[T](value: T) -> unit\n{\n    return again(some(value))\n}\n\n"
                      "fn again[T](value: T) -> unit\n{\n    return grow(value)\n}\n\n"
                      "fn start() -> unit\n{\n    return grow(1)\n}\n")
            write_pkg(root, module(declarations, bodies), fmt=True)
            self.assertEqual(diag_code(root), 29)

    def test_seeded_arithmetic_matches_integer_reference(self):
        import random

        rng = random.Random(61527)

        def expression(depth):
            if depth == 0:
                value = rng.randrange(-20, 21)
                return str(value), value
            left, aa = expression(depth - 1)
            right, bb = expression(depth - 1)
            operator = rng.choice(("+", "-", "*", "/", "%"))
            if operator in ("/", "%") and bb == 0:
                right, bb = "1", 1
            quotient = (abs(aa) // abs(bb)) * (-1 if (aa < 0) != (bb < 0) else 1) if bb else 0
            value = {"+": lambda: aa + bb, "-": lambda: aa - bb, "*": lambda: aa * bb,
                     "/": lambda: quotient, "%": lambda: aa - quotient * bb}[operator]()
            return f"({left} {operator} {right})", value

        assertions = []
        for _ in range(250):
            source, expected = expression(3)
            assertions.append(f"    core.test.assert_eq({source}, {expected})\n")
        with tempfile.TemporaryDirectory() as root:
            header = "task main() -> unit\n    effects { log }\n"
            write_pkg(root, module(header, header + '{\n    core.log.write("arithmetic")\n'
                                   + "".join(assertions) + "    return unit\n}\n",
                                   uses="use core.log { write }\nuse core.test { assert_eq }"), fmt=True)
            self.assertEqual(run_cli(root, "run", "demo.main"), (0, ""))

    def test_non_ascii_names_and_numbers_are_rejected(self):
        from gopyt.parser import parse_module

        for source in ("module démo\n", "module demo\n\nfn café() -> i64\n",
                       "module demo\n\nfn number() -> i64\n{\n    return ١\n}\n",
                       "module demo\n\nfn number() -> f64\n{\n    return 0.١\n}\n"):
            with self.subTest(source=source), self.assertRaises(CompileError):
                parse_module(source, "impl/demo.gopyt", "impl")

    def test_long_number_is_a_lexical_error(self):
        from gopyt.parser import parse_module

        with self.assertRaises(CompileError) as cm:
            parse_module("module demo\n\nfn huge() -> i64\n{\n    return " + "9" * 5000 + "\n}\n",
                         "impl/demo.gopyt", "impl")
        self.assertEqual(cm.exception.diag.code, 10)

    def test_duplicate_enum_payload_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            write_pkg(root, module("enum Choice {\n    Value {\n        item: i64\n        item: str\n    }\n}\n", ""), fmt=True)
            self.assertEqual(diag_code(root), 24)

    def test_build_directory_symlink_does_not_write_outside_package(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            write_pkg(root, module("fn answer() -> i64\n", "fn answer() -> i64\n{\n    return 1\n}\n"), fmt=True)
            Path(root, "build").symlink_to(outside, target_is_directory=True)
            code, out = run_cli(root, "check")
            self.assertEqual(code, 1)
            self.assertIn("GOPYT_E046", out)
            self.assertFalse(Path(outside, "out.gobyte").exists())

    def test_integer_conversion_keeps_union_variant(self):
        for width in ("i32", "u32", "u64"):
            with self.subTest(width=width), tempfile.TemporaryDirectory() as root:
                files = module(
                    "task main() -> bool\n    effects { log }\n",
                    "task main() -> bool\n    effects { log }\n{\n"
                    '    core.log.write("probe")\n'
                    f"    return match core.int.to_{width}(7) {{\n"
                    f"        {width} -> true\n        ConvertError {{ message }} -> false\n    }}\n}}\n",
                    uses=f"use core.int {{ to_{width} }}\nuse core.log {{ write }}\nuse core.status {{ ConvertError }}",
                )
                write_pkg(root, files, fmt=True)
                self.assertEqual(run_cli(root, "run", "demo.main"), (0, "true\n"))

    def test_union_equality_different_active_members_is_false(self):
        with tempfile.TemporaryDirectory() as root:
            header = "fn same(left: bool | i64, right: bool | i64) -> bool\n"
            files = module(
                header + "\ntask main() -> bool\n    effects { log }\n",
                header + "{\n    return left == right\n}\n\n"
                "task main() -> bool\n    effects { log }\n{\n"
                '    core.log.write("probe")\n    return same(true, 1)\n}\n',
                uses="use core.log { write }",
            )
            write_pkg(root, files, fmt=True)
            self.assertEqual(run_cli(root, "run", "demo.main"), (0, "false\n"))

    def test_enum_with_float_payload_has_no_equality(self):
        with tempfile.TemporaryDirectory() as root:
            declaration = "enum Choice {\n    Value {\n        number: f64\n    }\n}\n\n"
            header = "fn same(left: Choice, right: Choice) -> bool\n"
            write_pkg(root, module(declaration + header,
                                  header + "{\n    return left == right\n}\n"), fmt=True)
            self.assertEqual(diag_code(root), 99)

    def test_recursive_record_equality_terminates(self):
        with tempfile.TemporaryDirectory() as root:
            declaration = "type Node {\n    next: Node?\n}\n\n"
            header = "fn same(left: Node, right: Node) -> bool\n"
            write_pkg(root, module(declaration + header,
                                  header + "{\n    return left == right\n}\n"), fmt=True)
            self.assertEqual(run_cli(root, "check"), (0, ""))

    def test_source_directory_symlink_rejected_before_fmt(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            write_pkg(root, {}, lock=False)
            source = Path(outside, "demo.gopyt")
            original = "module demo\n\nfn answer()->i64\n"
            source.write_text(original)
            Path(root, "spec").symlink_to(outside, target_is_directory=True)
            code, out = run_cli(root, "fmt")
            self.assertEqual(code, 1)
            self.assertIn("GOPYT_E046", out)
            self.assertEqual(source.read_text(), original)

    def test_nested_directory_symlink_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            write_pkg(root, {"spec/demo.gopyt": "module demo\n"}, lock=False)
            Path(root, "spec", "hidden").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(CompileError) as cm:
                load_package(root)
            self.assertEqual(cm.exception.diag.code, 46)


class JsonAudit(unittest.TestCase):
    def test_json_whitespace_and_unicode_validity(self):
        art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_STR)])
        self.assertEqual(jsonc.decode(art, ' \t\r\n"ok" \n', 0), "ok")
        for text in ('"ok"\u00a0', '"\\ud800"'):
            with self.subTest(text=text), self.assertRaises(jsonc.ConvertFail):
                jsonc.decode(art, text, 0)

    def test_integer_union_round_trip_preserves_tag(self):
        art = gobyte.Artifact(texprs=[gobyte.TExpr(tag) for tag in
                                     (gobyte.TE_I32, gobyte.TE_I64, gobyte.TE_U32, gobyte.TE_U64)])
        art.texprs.append(gobyte.TExpr(gobyte.TE_UNION, members=(0, 1, 2, 3)))
        for width in ("i32", "i64", "u32", "u64"):
            with self.subTest(width=width):
                text = '{"' + width + '":7}'
                self.assertEqual(jsonc.encode(art, jsonc.decode(art, text, 4), 4), text)

    def test_decimal_integer_does_not_round_through_float(self):
        art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_I64)])
        self.assertEqual(jsonc.decode(art, "9007199254740993.0", 0), 9007199254740993)
        for text in ("9007199254740993.1", "1e1000000", "NaN", "Infinity"):
            with self.subTest(text=text), self.assertRaises(jsonc.ConvertFail):
                jsonc.decode(art, text, 0)


class ResourceAudit(unittest.TestCase):
    def test_native_collection_growth_checks_allocation_limit(self):
        from gopyt.natives import NATIVES
        from gopyt.vm import Trap

        with patch.object(ops, "MAX_ALLOC", 2):
            for name, args in (("core.str.concat", ["é", "x"]),
                               ("core.bytes.concat", [b"12", b"3"]),
                               ("core.map.set", [{"aa": 1, "bb": 2}, "cc", 3])):
                with self.subTest(name=name), self.assertRaises(Trap) as cm:
                    from types import SimpleNamespace
                    NATIVES[name](SimpleNamespace(check_cancelled=lambda: None), args, None)
                self.assertEqual(cm.exception.code, ops.TRAP_ALLOC)

    def test_file_io_rejects_nonregular_entries_without_blocking(self):
        from gopyt.files import regular_file

        with tempfile.TemporaryDirectory() as root:
            os.mkfifo(Path(root, "pipe"))
            for write in (False, True):
                with self.subTest(write=write), self.assertRaises(OSError):
                    with regular_file(root, "pipe", write=write):
                        self.fail("opened a FIFO")

class LoaderAudit(unittest.TestCase):
    def test_float_equality_is_rejected_before_execution(self):
        art = self.artifact(bytes([ops.CONST]) + struct.pack("<I", 1)
                            + bytes([ops.DUP, ops.EQ, ops.RETURN]))
        art.consts.append(gobyte.Const(gobyte.TAG_F64, 1.0))
        art.texprs[0] = gobyte.TExpr(gobyte.TE_BOOL)
        self.reject(art)

    def test_forged_stdlib_record_layout(self):
        art = self.artifact(bytes([ops.UNIT, ops.RETURN]))
        art.consts += [gobyte.Const(gobyte.TAG_STR, "core.status.NotFound"),
                       gobyte.Const(gobyte.TAG_STR, "payload")]
        art.types.append(gobyte.TypeDef(1, 1, [(2, 0)]))
        self.reject(art)

    def artifact(self, code):
        return gobyte.Artifact(
            consts=[gobyte.Const(gobyte.TAG_STR, "demo.main")],
            texprs=[gobyte.TExpr(gobyte.TE_UNIT)],
            funcs=[gobyte.Func(0, ops.KIND_FN, 0, 0, 0, ret=0, code=code)],
        )

    def reject(self, art):
        with self.assertRaises(CompileError) as cm:
            gobyte.decode(gobyte.encode(art))
        self.assertEqual(cm.exception.diag.code, 100)

    def test_bad_constant_index(self):
        self.reject(self.artifact(bytes([ops.CONST]) + struct.pack("<I", 99) + bytes([ops.RETURN])))

    def test_return_type_mismatch(self):
        art = self.artifact(bytes([ops.CONST]) + struct.pack("<I", 0) + bytes([ops.RETURN]))
        self.reject(art)  # returns the string function name from a unit function

    def test_native_argument_type_mismatch(self):
        art = self.artifact(bytes([ops.UNIT, ops.CALL_TASK]) + struct.pack("<IH", 1, 1) + bytes([ops.RETURN]))
        art.consts.append(gobyte.Const(gobyte.TAG_STR, "core.log.write"))
        art.texprs.append(gobyte.TExpr(gobyte.TE_STR))
        art.funcs[0].kind = ops.KIND_TASK
        art.funcs[0].effects = ops.effect_mask(["log"])
        art.funcs.append(gobyte.Func(1, ops.KIND_NATIVE, 1, 1, art.funcs[0].effects, [1], 0))
        self.reject(art)

    def test_join_type_mismatch(self):
        art = self.artifact(
            bytes([ops.CONST]) + struct.pack("<I", 1)
            + bytes([ops.JUMP_IF_FALSE]) + struct.pack("<i", 6)
            + bytes([ops.UNIT, ops.JUMP]) + struct.pack("<i", 5)
            + bytes([ops.CONST]) + struct.pack("<I", 0)
            + bytes([ops.POP, ops.UNIT, ops.RETURN])
        )
        art.consts.append(gobyte.Const(gobyte.TAG_BOOL, True))
        self.reject(art)

    def test_forged_provide_signature(self):
        art = self.artifact(b"")
        art.consts[0].value = "provide:core.convert.Json:unit:from_json"
        art.funcs[0].kind = ops.KIND_NATIVE
        self.reject(art)

    def test_seeded_bytecode_mutations_never_crash_decoder(self):
        import random

        rng = random.Random(20260905)
        data = gobyte.encode(self.artifact(bytes([ops.UNIT, ops.RETURN])))
        for _ in range(2000):
            mutant = bytearray(data)
            for _ in range(rng.randint(1, 4)):
                mutant[rng.randrange(len(mutant))] = rng.randrange(256)
            try:
                gobyte.decode(bytes(mutant))
            except CompileError as exc:
                self.assertEqual(exc.diag.code, 100)

    def test_dead_instruction_still_validates_indices(self):
        self.reject(self.artifact(bytes([ops.UNIT, ops.RETURN, ops.LOAD_LOCAL, 99, 0, ops.POP])))

    def test_falling_off_function_is_rejected(self):
        self.reject(self.artifact(bytes([ops.NOP])))

    def test_cyclic_type_expression_is_rejected(self):
        art = self.artifact(bytes([ops.UNIT, ops.RETURN]))
        art.texprs.append(gobyte.TExpr(gobyte.TE_LIST, 1))
        self.reject(art)


    def test_application_ffi_is_rejected(self):
        art = self.artifact(bytes([ops.UNIT, ops.RETURN]))
        art.funcs[0].kind = ops.KIND_TASK
        art.funcs[0].effects = ops.FFI_BIT
        self.reject(art)


@contextmanager
def http_endpoint(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01))
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class NetworkAudit(unittest.TestCase):
    def test_parallel_arm_keeps_its_modules_egress(self):
        from gopyt.cli import build, make_vm

        class Reply(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(204)
                self.end_headers()

        with http_endpoint(Reply) as origin, tempfile.TemporaryDirectory() as root:
            header = "task main() -> list[HttpResponse | HttpError]\n    effects { network, time }\n"
            files = module(
                header + f'\negress {{ "{origin}" }}\n',
                header + "{\n    return parallel max 1 timeout_ms 2000 {\n"
                f'        net.http.request(HttpRequest {{ method: HttpMethod.Get url: "{origin}/" body: core.bytes.from_str("") }})\n'
                "    }\n}\n",
                uses="use net.http { HttpMethod, HttpRequest, HttpResponse, request }\nuse core.bytes { from_str }\nuse core.status { HttpError }",
                spec_uses="use net.http { HttpResponse }\nuse core.status { HttpError }",
            )
            write_pkg(root, files, fmt=True)
            program, artifact, ids = build(root)
            vm = make_vm(root, program, artifact, ids)
            result = vm.call(ids["demo.main"], [])
            self.assertEqual(vm.type_name(result[0].type_id), "net.http.HttpResponse")
            self.assertEqual(result[0].fields[0], 204)

    def test_model_redirect_cannot_reach_an_unlisted_origin(self):
        hits = []

        class Destination(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"text":"escaped"}')

        with http_endpoint(Destination) as destination:
            class Redirect(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    self.send_response(302)
                    self.send_header("Location", destination + "/private")
                    self.end_headers()

            with http_endpoint(Redirect) as origin, tempfile.TemporaryDirectory() as root:
                files = module(
                    "task main() -> str | ModelError\n    effects { network, model }\n\n"
                    f'egress {{ "{origin}" }}\n',
                    "task main() -> str | ModelError\n    effects { network, model }\n{\n"
                    '    return core.model.complete("probe")\n}\n',
                    uses="use core.model { complete }\nuse core.status { ModelError }",
                    spec_uses="use core.status { ModelError }",
                )
                write_pkg(root, files, fmt=True)
                with patch.dict(os.environ, {"GOPYT_MODEL_URL": origin + "/start"}):
                    code, out = run_cli(root, "run", "demo.main")
                self.assertEqual(hits, [])
                self.assertEqual(code, 0)
                self.assertIn("core.status.ModelError", out)

    def test_origin_parser_accepts_query_without_path(self):
        from gopyt.check import _origin_of, normalize_origin
        from gopyt.natives import _origin

        for parser in (_origin_of, _origin):
            self.assertEqual(parser("https://api.example?value=1"), "https://api.example")
            self.assertIsNone(parser("https://user@api.example/path"))
            self.assertIsNone(parser("https://api.example\n/path"))
        self.assertEqual(normalize_origin("https://api.example:" + "0" * 5000 + "443"),
                         "https://api.example:443")
        self.assertIsNone(normalize_origin("https://api.example:" + "9" * 5000))


class DependencyAudit(unittest.TestCase):
    def test_digest_path_cannot_inject_additional_entries(self):
        import hashlib
        from gopyt.manifest import package_digest

        with tempfile.TemporaryDirectory() as root:
            write_pkg(root, {}, lock=False)
            injected = "impl/a.txt\nsha256:" + hashlib.sha256(b"alpha").hexdigest() + "\nimpl/b.txt"
            path = Path(root, injected)
            path.parent.mkdir(parents=True)
            path.write_bytes(b"bravo")
            with self.assertRaises(CompileError) as cm:
                package_digest(root)
            self.assertEqual(cm.exception.diag.code, 46)

    def test_transitive_lock_paths_and_order(self):
        with tempfile.TemporaryDirectory() as root:
            write_pkg(root, {}, name="app", lock=False)
            Path(root, "vendor/middle/vendor/alpha").mkdir(parents=True)
            write_pkg(str(Path(root, "vendor/middle")), {
                "spec/middle.gopyt": "module middle\n",
            }, name="middle", lock=False)
            write_pkg(str(Path(root, "vendor/middle/vendor/alpha")), {
                "spec/alpha.gopyt": "module alpha\n",
            }, name="alpha", lock=False)
            for path, name, dep, target in ((root, "app", "middle", "vendor/middle"),
                                          (str(Path(root, "vendor/middle")), "middle", "alpha", "vendor/alpha")):
                Path(path, "gopyt.toml").write_text(
                    f'name = "{name}"\nversion = "0.1.0"\n\n[deps]\n{dep} = {{ path = "{target}" }}\n')
            pkg = load_package(root)
            self.assertEqual([(p[0], p[2]) for p in pkg.packages],
                             [("app", "."), ("alpha", "vendor/middle/vendor/alpha"), ("middle", "vendor/middle")])

    def test_dependency_formatting_is_checked(self):
        from gopyt.testing import write_lock

        with tempfile.TemporaryDirectory() as root:
            write_pkg(root, {}, name="app", lock=False)
            dep = Path(root, "helper")
            dep.mkdir()
            write_pkg(str(dep), {"spec/helper.gopyt": "module helper\n\n\n"}, name="helper", lock=False)
            Path(root, "gopyt.toml").write_text('name = "app"\nversion = "0.1.0"\n\n[deps]\nhelper = { path = "helper" }\n')
            write_lock(root)
            self.assertEqual(diag_code(root), 4)


class EvolutionAudit(unittest.TestCase):
    def test_candidates_keep_path_dependencies_resolvable(self):
        import shutil
        from gopyt import evolve

        with tempfile.TemporaryDirectory() as work:
            root = str(Path(work, "auth"))
            shutil.copytree(Path(__file__).resolve().parents[1] / "examples/auth", root)
            helper = Path(root, "helper")
            helper.mkdir()
            write_pkg(str(helper), {"spec/helper.gopyt": "module helper\n"}, name="helper", lock=False)
            manifest = Path(root, "gopyt.toml")
            manifest.write_text(manifest.read_text() + '\n[deps]\nhelper = { path = "helper" }\n')
            evolve._write_lock(root)
            result = evolve.propose(root, True, 2)
            self.assertEqual(result.kind, "Applied", result.message)
            self.assertEqual(run_cli(root, "check"), (0, ""))

    def test_candidate_does_not_rewrite_string_literals(self):
        from gopyt.evolve import candidates

        with tempfile.TemporaryDirectory() as root:
            files = module("fn hint() -> str\n", 'fn hint() -> str\n{\n    return "core.limit.allow(key, 8, 1000)"\n}\n')
            write_pkg(root, files, fmt=True)
            self.assertEqual(candidates(root, 4), [])

    def test_timeout_leaves_live_sources_and_lock_unchanged(self):
        import shutil
        from gopyt.evolve import propose
        from gopyt.manifest import package_digest

        with tempfile.TemporaryDirectory() as work:
            root = str(Path(work, "auth"))
            shutil.copytree(Path(__file__).resolve().parents[1] / "examples/auth", root)
            before = package_digest(root)
            lock = Path(root, "gopyt.lock").read_bytes()
            result = propose(root, True, 2, timeout_ms=1)
            self.assertEqual((result.kind, result.message), ("EvolveError", "timeout"))
            self.assertEqual(package_digest(root), before)
            self.assertEqual(Path(root, "gopyt.lock").read_bytes(), lock)

    def test_apply_failure_rolls_back_sources(self):
        import shutil
        from gopyt import evolve
        from gopyt.manifest import package_digest

        with tempfile.TemporaryDirectory() as work:
            root = str(Path(work, "auth"))
            shutil.copytree(Path(__file__).resolve().parents[1] / "examples/auth", root)
            before = package_digest(root)
            plan = evolve._prepare(root, 2)
            original = evolve.atomic_write

            def fail_lock(root, path, data):
                if path == "gopyt.lock":
                    raise OSError("injected write failure")
                return original(root, path, data)

            with patch.object(evolve, "atomic_write", fail_lock):
                result = evolve._apply(root, plan)
            self.assertEqual(result.kind, "EvolveError")
            self.assertEqual(package_digest(root), before)
            self.assertEqual(run_cli(root, "check"), (0, ""))


class HttpBoundaryAudit(unittest.TestCase):
    def setUp(self):
        from gopyt.test_vm import HttpEdges

        self.fixture = HttpEdges()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_invalid_content_lengths_return_400(self):
        import socket

        for length in ("-1", "garbage", "1\r\nContent-Length: 2"):
            with self.subTest(length=length), socket.create_connection(("127.0.0.1", self.fixture.port), timeout=2) as connection:
                connection.sendall(f"POST /charge HTTP/1.1\r\nHost: localhost\r\nContent-Length: {length}\r\n\r\n".encode())
                self.assertIn(b" 400 ", connection.recv(1024))

    def test_bad_input_observation_uses_route_template(self):
        status, _body = self.fixture.send("/charge", "POST", b'{"private":"secret"}', "application/json")
        self.assertEqual(status, 400)
        obs = self.fixture.vm.observe
        self.assertGreater(obs.cms.estimate("http:/charge:400"), 0)
        self.assertFalse(any("private" in row or "secret" in row for row in obs.reservoir.items))


class HttpQueueAudit(unittest.TestCase):
    def test_worker_count_and_accepted_queue_are_bounded(self):
        import socket
        import time
        from gopyt import server
        from gopyt.test_vm import HttpEdges

        self.assertEqual((server.MAX_HANDLERS, server.QUEUE), (64, 1024))
        fixture = HttpEdges()
        connections = []
        with patch.object(server, "MAX_HANDLERS", 2), patch.object(server, "QUEUE", 2):
            fixture.setUp()
            try:
                for _ in range(4):
                    connection = socket.create_connection(("127.0.0.1", fixture.port), timeout=2)
                    connection.sendall(b"GET /echo/user HTTP/1.1\r\n")  # hold workers at the header boundary
                    connections.append(connection)
                    time.sleep(0.01)
                httpd = fixture.vm.httpd
                deadline = time.monotonic() + 2
                while httpd.pending.qsize() < 2 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(len(httpd.workers), 2)
                self.assertEqual(httpd.pending.qsize(), 2)
                with socket.create_connection(("127.0.0.1", fixture.port), timeout=2) as excess:
                    excess.sendall(b"GET /echo/user HTTP/1.1\r\nHost: localhost\r\n\r\n")
                    self.assertIn(b" 503 ", excess.recv(1024))
            finally:
                fixture.tearDown()
                for connection in connections:
                    connection.close()
            deadline = time.monotonic() + 2
            while any(worker.is_alive() for worker in httpd.workers) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(any(worker.is_alive() for worker in httpd.workers))
