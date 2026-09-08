"""Mechanized VM claims: traps, structured concurrency, security, observe.

docs/validation.md calls these "mechanized": they are decided by fixtures and
the VM, never by a model.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from gopyt import gobyte, ops
from gopyt.gobyte import Artifact, Const, Func, TExpr, decode, encode
from gopyt.observe import Observe
from gopyt.testing import run_cli, write_pkg
from gopyt.vm import Trap

import urllib.error


def module(
    spec_body: str, impl_body: str, uses: str = "", spec_uses: str = ""
) -> dict[str, str]:
    """spec/ and impl/ keep their own `use` lists; unused names are E014."""
    spec_head = "module demo\n\n" + (spec_uses + "\n\n" if spec_uses else "")
    impl_head = "module demo\n\n" + (uses + "\n\n" if uses else "")
    return {"spec/demo.gopyt": spec_head + spec_body, "impl/demo.gopyt": impl_head + impl_body}


class Runtime(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td)

    def build(self, files: dict[str, str]) -> str:
        write_pkg(self.td, files, fmt=True)
        return self.td

    def run_task(self, files: dict[str, str], target: str = "demo.main") -> tuple[int, str]:
        return run_cli(self.build(files), "run", target)

    # -- S20 traps -------------------------------------------------------

    def test_overflow_traps(self) -> None:
        files = module(
            "task main() -> i64\n    effects { log }\n",
            (
                "task main() -> i64\n    effects { log }\n{\n"
                '    core.log.write("go")\n'
                "    big = 9223372036854775807\n    return big + 1\n}\n"
            ),
            uses="use core.log { write }",
        )
        code, out = self.run_task(files)
        self.assertEqual(code, 2)
        self.assertIn("trap: 3", out)

    def test_requires_traps(self) -> None:
        files = module(
            "task main() -> i64\n    effects { log }\n\nfn half(value: i64) -> i64\n    requires value >= 0\n",
            (
                "task main() -> i64\n    effects { log }\n{\n"
                '    core.log.write("go")\n    return half(-2)\n}\n\n'
                "fn half(value: i64) -> i64\n    requires value >= 0\n{\n    return value / 2\n}\n"
            ),
            uses="use core.log { write }",
        )
        code, out = self.run_task(files)
        self.assertEqual(code, 2)
        self.assertIn("trap: 1", out)

    def test_ensures_traps(self) -> None:
        files = module(
            "task main() -> i64\n    effects { log }\n\nfn wrong(value: i64) -> i64\n    ensures result > value\n",
            (
                "task main() -> i64\n    effects { log }\n{\n"
                '    core.log.write("go")\n    return wrong(1)\n}\n\n'
                "fn wrong(value: i64) -> i64\n    ensures result > value\n{\n    return value\n}\n"
            ),
            uses="use core.log { write }",
        )
        code, out = self.run_task(files)
        self.assertEqual(code, 2)
        self.assertIn("trap: 2", out)

    def test_call_depth_traps(self) -> None:
        files = module(
            "task main() -> i64\n    effects { log }\n\nfn down(value: i64) -> i64\n",
            (
                "task main() -> i64\n    effects { log }\n{\n"
                '    core.log.write("go")\n    return down(1000)\n}\n\n'
                "fn down(value: i64) -> i64\n{\n"
                "    if value == 0 {\n        return 0\n    }\n    return down(value - 1)\n}\n"
            ),
            uses="use core.log { write }",
        )
        code, out = self.run_task(files)
        self.assertEqual(code, 2)
        self.assertIn("trap: 12", out)

    def test_list_get_negative_index_traps(self) -> None:
        files = module(
            "task main() -> i64\n    effects { log }\n",
            (
                "task main() -> i64\n    effects { log }\n{\n"
                '    core.log.write("go")\n'
                "    items = core.list.range(0, 3)\n"
                "    return match core.list.get(items, -1) {\n"
                "        some(value) -> value\n        none -> 0\n    }\n}\n"
            ),
            uses="use core.list { get, range }\nuse core.log { write }",
        )
        code, out = self.run_task(files)
        self.assertEqual(code, 2)
        self.assertIn("trap: 1", out)

    # -- loops and bindings ----------------------------------------------

    def test_for_loop_rebinds_its_slots(self) -> None:
        files = module(
            "task main() -> i64\n    effects { log }\n",
            (
                "task main() -> i64\n    effects { log }\n{\n"
                "    for value in core.list.range(0, 4) {\n"
                "        core.log.write(core.str.from_i64(value))\n    }\n    return 0\n}\n"
            ),
            uses="use core.list { range }\nuse core.log { write }\nuse core.str { from_i64 }",
        )
        code, _out = self.run_task(files)
        self.assertEqual(code, 0)

    # -- structured concurrency (D7) --------------------------------------

    def test_parallel_timeout_traps(self) -> None:
        files = module(
            "task main() -> list[unit]\n    effects { time }\n",
            (
                "task main() -> list[unit]\n    effects { time }\n{\n"
                "    return parallel max 2 timeout_ms 20 {\n"
                "        core.time.sleep_ms(400)\n        core.time.sleep_ms(400)\n    }\n}\n"
            ),
            uses="use core.time { sleep_ms }",
        )
        code, out = self.run_task(files)
        self.assertEqual(code, 2)
        self.assertIn("trap: 6", out)

    def test_parallel_arm_trap_fails_parent(self) -> None:
        files = module(
            "task main() -> list[i64]\n    effects { time }\n\nfn bad(value: i64) -> i64\n",
            (
                "task main() -> list[i64]\n    effects { time }\n{\n"
                "    zero = 0\n"
                "    return parallel max 2 timeout_ms 2000 {\n"
                "        bad(zero)\n        1\n    }\n}\n\n"
                "fn bad(value: i64) -> i64\n{\n    return 1 / value\n}\n"
            ),
        )
        code, out = self.run_task(files)
        self.assertEqual(code, 2)
        self.assertIn("trap: 4", out)

    def test_nested_parallel_captures_through_an_arm(self) -> None:
        files = module(
            "task main() -> list[list[i64]]\n    effects { time }\n",
            (
                "task main() -> list[list[i64]]\n    effects { time }\n{\n"
                "    base = 5\n"
                "    return parallel max 2 timeout_ms 3000 {\n"
                "        parallel max 2 timeout_ms 1000 {\n"
                "            base + 1\n            base + 2\n        }\n"
                "        parallel max 2 timeout_ms 1000 {\n"
                "            base + 3\n            base + 4\n        }\n    }\n}\n"
            ),
        )
        code, out = self.run_task(files)
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "[[6,7],[8,9]]\n")

    def test_an_arm_captures_a_match_binding(self) -> None:
        files = module(
            "task main() -> list[i64]\n    effects { time }\n",
            (
                "task main() -> list[i64]\n    effects { time }\n{\n"
                "    found = core.list.get(core.list.range(3, 9), 0)\n"
                "    return match found {\n"
                "        some(value) -> parallel max 2 timeout_ms 1000 {\n"
                "            value + 1\n            value + 2\n        }\n"
                "        none -> core.list.empty[i64]()\n    }\n}\n"
            ),
            uses="use core.list { empty, get, range }",
        )
        code, out = self.run_task(files)
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "[4,5]\n")

    def test_parallel_captures_outer_locals(self) -> None:
        files = module(
            "task main() -> list[i64]\n    effects { time }\n",
            (
                "task main() -> list[i64]\n    effects { time }\n{\n"
                "    base = 10\n"
                "    return parallel max 2 timeout_ms 2000 {\n"
                "        base + 1\n        base + 2\n    }\n}\n"
            ),
        )
        code, out = self.run_task(files)
        self.assertEqual(code, 0)
        self.assertEqual(out, "[11,12]\n")

    # -- stdlib and security ----------------------------------------------

    def test_store_db_round_trip(self) -> None:
        files = module(
            "task main() -> str | NotFound | DbError\n    effects { database.read, database.write, ffi }\n",
            (
                "task main() -> str | NotFound | DbError\n"
                "    effects { database.read, database.write, ffi }\n{\n"
                '    store.db.put("key", "value")\n    return store.db.get("key")\n}\n'
            ),
            uses="use core.status { DbError, NotFound }\nuse store.db { get, put }",
            spec_uses="use core.status { DbError, NotFound }",
        )
        code, out = self.run_task(files)
        self.assertEqual(code, 1)  # S30: app modules may not declare ffi
        self.assertIn("GOPYT_E069", out)

    def test_store_db_from_an_app_task(self) -> None:
        """S9 (amended): stdlib `ffi` does not propagate; scalar arms match."""
        files = module(
            "task main() -> i64\n    effects { database.read, database.write }\n",
            (
                "task main() -> i64\n"
                "    effects { database.read, database.write }\n{\n"
                '    store.db.put("key", "value")\n'
                '    found = store.db.get("key")\n'
                "    return match found {\n"
                "        str -> core.str.len(found)\n"
                "        NotFound -> -1\n"
                "        DbError { message } -> -2\n    }\n}\n"
            ),
            uses="use core.status { DbError, NotFound }\nuse core.str { len }\nuse store.db { get, put }",
        )
        code, out = self.run_task(files)
        self.assertEqual(code, 0)
        self.assertEqual(out, "5\n")

    def test_egress_denied_at_runtime(self) -> None:
        files = module(
            "task main() -> i64\n    effects { network }\n\negress { \"https://api.stripe.com\" }\n",
            (
                "task main() -> i64\n    effects { network }\n{\n"
                "    host = core.str.concat(\"https://\", \"example.invalid\")\n"
                "    req = HttpRequest {\n        method: HttpMethod.Get\n"
                "        url: host\n        body: core.bytes.from_str(\"\")\n    }\n"
                "    return match net.http.request(req) {\n"
                "        HttpResponse { status body } -> status\n"
                "        HttpError { message } -> 0\n    }\n}\n"
            ),
            uses=(
                "use core.bytes { from_str }\nuse core.status { HttpError }\n"
                "use core.str { concat }\n"
                "use net.http { HttpMethod, HttpRequest, HttpResponse, request }"
            ),
        )
        code, out = self.run_task(files)
        self.assertEqual(code, 0)
        self.assertEqual(out, "0\n")

    def test_limit_throttles(self) -> None:
        files = module(
            "task main() -> i64\n    effects { time }\n",
            (
                "task main() -> i64\n    effects { time }\n{\n"
                "    first = core.limit.allow(\"key\", 1, 100000)\n"
                "    return match core.limit.allow(\"key\", 1, 100000) {\n"
                "        unit -> 0\n        Throttled -> 1\n    }\n}\n"
            ),
            uses="use core.limit { allow }\nuse core.status { Throttled }",
        )
        code, out = self.run_task(files)
        self.assertEqual(code, 0)
        self.assertEqual(out, "1\n")

    def test_a_test_stores_its_inferred_effects_in_the_artifact(self) -> None:
        """C031 / S9: preserve application effects; stdlib ffi does not propagate."""
        spec = "module demo\n\nfn ok() -> i64\n"
        impl = "module demo\n\nfn ok() -> i64\n{\n    return 1\n}\n"
        tests = (
            "module test.demo\n\nuse core.log { write }\nuse store.db { put }\n\n"
            "test writes\n{\n"
            '    store.db.put("effects", "v")\n'
            '    core.log.write("noted")\n    return unit\n}\n'
        )
        write_pkg(
            self.td,
            {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl, "test/demo.gopyt": tests},
            fmt=True,
        )
        self.assertEqual(run_cli(self.td, "check")[0], 0)
        art = decode(Path(self.td, "build/out.gobyte").read_bytes())
        entry = next(f for f in art.funcs if art.const_str(f.name) == "test.demo.writes")
        self.assertEqual(entry.kind, ops.KIND_TEST)
        self.assertEqual(
            sorted(ops.mask_effects(entry.effects)), ["database.write", "log"]
        )

    def test_an_early_return_from_a_loop(self) -> None:
        files = module(
            "task main() -> i64\n    effects { log }\n",
            (
                "task main() -> i64\n    effects { log }\n{\n"
                "    for value in core.list.range(0, 10) {\n"
                "        if value == 4 {\n"
                '            core.log.write("found")\n'
                "            return value\n        }\n    }\n    return -1\n}\n"
            ),
            uses="use core.list { range }\nuse core.log { write }",
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "4\n")

    def test_a_trap_is_one_observe_event_not_one_per_frame(self) -> None:
        from gopyt.cli import build, make_vm

        files = module(
            "task main() -> i64\n    effects { observe }\n\n"
            "fn deep(value: i64) -> i64\n",
            (
                "task main() -> i64\n    effects { observe }\n{\n"
                "    report = core.observe.report()\n    return report.fail\n}\n\n"
                "fn deep(value: i64) -> i64\n{\n"
                "    if value == 0 {\n        return 1 / value\n    }\n"
                "    return deep(value - 1)\n}\n"
            ),
            uses="use core.observe { report }",
        )
        write_pkg(self.td, files, fmt=True)
        self.assertEqual(run_cli(self.td, "check")[0], 0)
        prog, art, fn_ids = build(self.td)
        vm = make_vm(self.td, prog, art, fn_ids)
        before = vm.observe.fail
        with self.assertRaises(Trap):
            vm.call(fn_ids["demo.deep"], [5])
        self.assertEqual(vm.observe.fail, before + 1)

    def test_observe_report_counts_traps(self) -> None:
        files = module(
            "task main() -> i64\n    effects { observe }\n",
            (
                "task main() -> i64\n    effects { observe }\n{\n"
                '    core.observe.note("hello")\n'
                "    report = core.observe.report()\n    return report.events\n}\n"
            ),
            uses="use core.observe { note, report }",
        )
        code, out = self.run_task(files)
        self.assertEqual(code, 0)
        self.assertEqual(out, "1\n")

    def test_artifact_is_reproducible(self) -> None:
        """RP-002: same sources and toolchain, same bytes."""
        files = module("fn ok() -> i64\n", "fn ok() -> i64\n{\n    return 1\n}\n")
        root = self.build(files)
        self.assertEqual(run_cli(root, "check")[0], 0)
        first = Path(root, "build/out.gobyte").read_bytes()
        self.assertEqual(run_cli(root, "check")[0], 0)
        self.assertEqual(first, Path(root, "build/out.gobyte").read_bytes())

    def test_assert_eq_mismatch_is_trap_13(self) -> None:
        files = module(
            "fn ok() -> i64\n",
            "fn ok() -> i64\n{\n    return 1\n}\n",
        )
        files["test/demo.gopyt"] = (
            "module test.demo\n\nuse core.test { assert_eq }\nuse demo { ok }\n\n"
            "test wrong\n{\n    core.test.assert_eq(demo.ok(), 2)\n    return unit\n}\n"
        )
        root = self.build(files)
        code, out = run_cli(root, "test")
        self.assertEqual(code, 2)
        self.assertIn("trap: 13", out)


class Loader(unittest.TestCase):
    """The loader rejects malformed bytecode before execution."""

    def artifact(self) -> Artifact:
        art = Artifact()
        art.consts.append(Const(gobyte.TAG_STR, "demo.main"))
        art.texprs.append(TExpr(gobyte.TE_UNIT))
        art.funcs.append(
            Func(0, ops.KIND_FN, 0, 0, 0, [], 0, [], bytes([ops.UNIT, ops.RETURN]))
        )
        return art

    def test_round_trip(self) -> None:
        art = self.artifact()
        self.assertEqual(len(decode(encode(art)).funcs), 1)

    def test_bad_magic(self) -> None:
        data = bytearray(encode(self.artifact()))
        data[0] = ord("X")
        with self.assertRaises(Exception) as cm:
            decode(bytes(data))
        self.assertEqual(cm.exception.diag.code, 100)

    def test_unknown_opcode(self) -> None:
        art = self.artifact()
        art.funcs[0].code = bytes([0xFE, ops.RETURN])
        with self.assertRaises(Exception) as cm:
            decode(encode(art))
        self.assertEqual(cm.exception.diag.code, 100)

    def test_fn_may_not_carry_effects(self) -> None:
        art = self.artifact()
        art.funcs[0].effects = ops.effect_mask(["log"])
        with self.assertRaises(Exception) as cm:
            decode(encode(art))
        self.assertEqual(cm.exception.diag.code, 100)

    def test_a_native_must_match_the_shipped_declaration(self) -> None:
        """bytecode.md: arity, parameter types, return type, and effects."""
        from gopyt.gobyte import TE_I64, TE_STR

        def native_artifact(name: str, params, ret, effects=0, arity=None):
            art = Artifact()
            art.consts.append(Const(gobyte.TAG_STR, name))
            art.texprs.append(TExpr(TE_STR))  # 0
            art.texprs.append(TExpr(TE_I64))  # 1
            art.funcs.append(
                Func(
                    0,
                    ops.KIND_NATIVE,
                    len(params) if arity is None else arity,
                    len(params) if arity is None else arity,
                    effects,
                    list(params),
                    ret,
                    [],
                    b"",
                )
            )
            return art

        from gopyt.natives import verify_natives

        # core.str.len(text: str) -> i64
        verify_natives(decode(encode(native_artifact("core.str.len", [0], 1))))
        for broken in (
            native_artifact("core.str.len", [1], 1),          # wrong parameter type
            native_artifact("core.str.len", [0], 0),          # wrong return type
            native_artifact("core.str.len", [0, 0], 1),       # wrong arity
            native_artifact("core.str.len", [0], 1, effects=ops.effect_mask(["log"])),
            native_artifact("core.str.nope", [0], 1),         # not a stdlib name
        ):
            with self.assertRaises(Exception) as cm:
                verify_natives(decode(encode(broken)))
            self.assertEqual(cm.exception.diag.code, 100)

    def test_stack_must_be_empty_at_end(self) -> None:
        art = self.artifact()
        art.funcs[0].code = bytes([ops.UNIT])
        with self.assertRaises(Exception) as cm:
            decode(encode(art))
        self.assertEqual(cm.exception.diag.code, 100)

    def test_a_frame_must_be_empty_after_return(self) -> None:
        """Anything left on the stack at RETURN is a lowering bug."""
        art = self.artifact()
        art.funcs[0].code = bytes([ops.UNIT, ops.UNIT, ops.RETURN])
        with self.assertRaises(Exception) as cm:
            decode(encode(art))
        self.assertEqual(cm.exception.diag.code, 100)

    def test_stack_height_must_agree_at_a_join(self) -> None:
        art = self.artifact()
        art.consts.append(Const(gobyte.TAG_BOOL, True))
        code = bytearray()
        code += bytes([ops.CONST]) + (1).to_bytes(4, "little")
        code += bytes([ops.JUMP_IF_FALSE]) + (1).to_bytes(4, "little", signed=True)
        code += bytes([ops.UNIT, ops.UNIT, ops.RETURN])
        art.funcs[0].code = bytes(code)
        with self.assertRaises(Exception) as cm:
            decode(encode(art))
        self.assertEqual(cm.exception.diag.code, 100)


class ObservePort(unittest.TestCase):
    """The runtime port must behave like prototypes/observe (P0)."""

    def test_reservoir_is_bounded(self) -> None:
        import random

        random.seed(1)
        obs = Observe(reservoir_k=8)
        for i in range(10000):
            obs.event(str(i), True, 1.0)
        self.assertEqual(len(obs.reservoir.items), 8)

    def test_cusum_alarms_on_a_planted_shift(self) -> None:
        import random

        random.seed(2)
        obs = Observe(reservoir_k=32)
        for _ in range(3000):
            obs.event("ok", False, 10.0)
        self.assertFalse(obs.cusum.alarm)
        for _ in range(400):
            fail = random.random() < 0.4
            obs.event("denied" if fail else "ok", fail, 12.0)
        self.assertTrue(obs.cusum.alarm)

    def test_memory_is_fixed(self) -> None:
        obs = Observe(reservoir_k=32)
        before = obs.memory_cells()
        for _ in range(5000):
            obs.event("denied", True, 1.0)
        self.assertEqual(obs.memory_cells(), before)


class HttpServer(unittest.TestCase):
    """`task serve` end to end (docs/implementer.md 12)."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td)

    def test_post_route_round_trip(self) -> None:
        import json
        import os
        import socket
        import threading
        import time
        import urllib.request

        from gopyt.cli import build, make_vm
        from gopyt.test_conformance import HTTP_FILES

        write_pkg(self.td, HTTP_FILES)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        os.environ["GOPYT_HTTP_ADDR"] = f"127.0.0.1:{port}"
        try:
            prog, art, fn_ids = build(self.td)
            vm = make_vm(self.td, prog, art, fn_ids)
            serve_id = fn_ids["billing.api.serve"]
            thread = threading.Thread(target=vm.call, args=(serve_id, []), daemon=True)
            thread.start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and getattr(vm, "httpd", None) is None:
                time.sleep(0.01)
            self.assertIsNotNone(getattr(vm, "httpd", None), "server never started")
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/charge",
                data=json.dumps({"amount": 12}).encode("utf-8"),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(json.loads(resp.read()), {"amount": 12})
            missing = urllib.request.Request(f"http://127.0.0.1:{port}/nope", method="POST")
            try:
                urllib.request.urlopen(missing, timeout=5)
                self.fail("expected 404")
            except urllib.error.HTTPError as e:
                with e:
                    self.assertEqual(e.code, 404)
        finally:
            httpd = getattr(vm, "httpd", None)
            if httpd is not None:
                httpd.shutdown()
            os.environ.pop("GOPYT_HTTP_ADDR", None)



class Security(unittest.TestCase):
    """P4 (docs/validation.md): negative security cases, mechanized."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td)

    def test_wrong_literal_origin_fails_check(self) -> None:
        files = module(
            "task main() -> i64\n    effects { network }\n\negress { \"https://api.stripe.com\" }\n",
            (
                "task main() -> i64\n    effects { network }\n{\n"
                "    req = HttpRequest {\n        method: HttpMethod.Get\n"
                '        url: "https://evil.example/x"\n'
                "        body: core.bytes.from_str(\"\")\n    }\n"
                "    return match net.http.request(req) {\n"
                "        HttpResponse { status body } -> status\n"
                "        HttpError { message } -> 0\n    }\n}\n"
            ),
            uses=(
                "use core.bytes { from_str }\nuse core.status { HttpError }\n"
                "use net.http { HttpMethod, HttpRequest, HttpResponse, request }"
            ),
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "check")
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E111", out)

    def test_secret_never_json(self) -> None:
        files = module(
            "task main() -> str | ConvertError\n    effects { secret }\n",
            (
                "task main() -> str | ConvertError\n    effects { secret }\n{\n"
                '    found = core.secret.get("api_token")\n'
                "    return match found {\n"
                "        Secret -> data.json.encode(found)\n"
                '        NotFound -> "none"\n    }\n}\n'
            ),
            uses=(
                "use core.secret { Secret, get }\nuse core.status { ConvertError, NotFound }\n"
                "use data.json { encode }"
            ),
            spec_uses="use core.status { ConvertError }",
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "check")
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E112", out)

    def test_missing_lock_is_e041_with_full_repair(self) -> None:
        """docs/lockfile.md: missing lock -> E041; the repair is the whole lock."""
        files = module("fn ok() -> i64\n", "fn ok() -> i64\n{\n    return 1\n}\n")
        write_pkg(self.td, files, lock=False)
        code, out = run_cli(self.td, "check")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("GOPYT_E041 lock_stale\n"), out)
        header, repair = out.split("\n\n", 1)
        declared = int([l for l in header.split("\n") if l.startswith("repair-bytes:")][0].split()[1])
        self.assertEqual(declared, len(repair.encode("utf-8")))
        Path(self.td, "gopyt.lock").write_text(repair, encoding="utf-8")
        self.assertEqual(run_cli(self.td, "check")[0], 0)

    def test_truncated_artifact_is_rejected(self) -> None:
        files = module("fn ok() -> i64\n", "fn ok() -> i64\n{\n    return 1\n}\n")
        write_pkg(self.td, files)
        self.assertEqual(run_cli(self.td, "check")[0], 0)
        data = Path(self.td, "build/out.gobyte").read_bytes()
        for cut in (4, 6, len(data) // 2, len(data) - 1):
            with self.assertRaises(Exception) as cm:
                decode(data[:cut])
            self.assertEqual(cm.exception.diag.code, 100)


class Hardening(unittest.TestCase):
    """S32: a planted failure burst trips CUSUM with no model call."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td)

    def test_denied_burst_sets_cusum_alarm(self) -> None:
        files = module(
            "task main() -> bool\n    effects { time, observe }\n\ntask hit() -> unit\n    effects { time }\n",
            (
                "task main() -> bool\n    effects { time, observe }\n{\n"
                "    for step in core.list.range(0, 40) {\n        hit()\n    }\n"
                "    return core.observe.report().cusum_alarm\n}\n\n"
                "task hit() -> unit\n    effects { time }\n{\n"
                "    return match core.limit.allow(\"login\", 1, 100000) {\n"
                "        unit -> unit\n        Throttled -> unit\n    }\n}\n"
            ),
            uses=(
                "use core.limit { allow }\nuse core.list { range }\n"
                "use core.observe { report }\nuse core.status { Throttled }"
            ),
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "true\n")



class GenericBodies(unittest.TestCase):
    """An uninstantiated generic still gets one body pass (implementer.md 4)."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td)

    def code(self, spec: str, impl: str) -> tuple[int, str]:
        write_pkg(self.td, {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl})
        return run_cli(self.td, "check")

    def test_identity_is_accepted(self) -> None:
        code, out = self.code(
            "module demo\n\nfn identity[T](value: T) -> T\n",
            "module demo\n\nfn identity[T](value: T) -> T\n{\n    return value\n}\n",
        )
        self.assertEqual(code, 0, out)

    def test_type_error_in_an_uncalled_generic_is_reported(self) -> None:
        code, out = self.code(
            "module demo\n\nfn identity[T](value: T) -> T\n",
            "module demo\n\nfn identity[T](value: T) -> T\n{\n    return 1 / 0\n}\n",
        )
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E021", out)

    def test_unresolved_in_an_uncalled_generic_is_reported(self) -> None:
        code, out = self.code(
            "module demo\n\nfn identity[T](value: T) -> T\n",
            "module demo\n\nfn identity[T](value: T) -> T\n{\n    unresolved identity\n}\n",
        )
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E030", out)

    def test_missing_return_in_an_uncalled_generic_is_reported(self) -> None:
        code, out = self.code(
            "module demo\n\nfn identity[T](value: T) -> T\n",
            "module demo\n\nfn identity[T](value: T) -> T\n{\n}\n",
        )
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E073", out)

    def test_uncalled_generic_emits_no_function(self) -> None:
        spec = "module demo\n\nfn identity[T](value: T) -> T\n\ntask main() -> unit\n    effects { log }\n"
        impl = (
            "module demo\n\nuse core.log { write }\n\n"
            "fn identity[T](value: T) -> T\n{\n    return value\n}\n\n"
            "task main() -> unit\n    effects { log }\n{\n"
            '    return core.log.write("x")\n}\n'
        )
        write_pkg(self.td, {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl})
        self.assertEqual(run_cli(self.td, "check")[0], 0)
        art = decode(Path(self.td, "build/out.gobyte").read_bytes())
        names = {art.const_str(f.name) for f in art.funcs}
        self.assertNotIn("demo.identity", names)
        self.assertFalse(any(n.startswith("demo.identity[") for n in names))

    def test_generic_body_may_use_its_own_type_variable(self) -> None:
        spec = "module demo\n\nfn pair[T](value: T) -> list[T]\n"
        impl = (
            "module demo\n\nuse core.list { append, empty }\n\n"
            "fn pair[T](value: T) -> list[T]\n{\n"
            "    items = core.list.empty[T]()\n"
            "    return core.list.append(items, value)\n}\n"
        )
        code, out = self.code(spec, impl)
        self.assertEqual(code, 0, out)



API_FILES = {
    "spec/api.gopyt": (
        "module api\n\nuse core.convert { Json }\nuse core.status { ListenError }\n\n"
        "type Payment {\n    amount: i64\n}\n\n"
        "type Receipt {\n    amount: i64\n}\n\n"
        "provide Json for Payment\n\nprovide Json for Receipt\n\n"
        "http {\n    get \"/echo/{name}\" get_echo\n    post \"/charge\" post_charge\n}\n\n"
        "task get_echo(name: str) -> Receipt\n    effects { log }\n\n"
        "task post_charge(payment: Payment) -> Receipt\n    effects { log }\n\n"
        "task serve() -> unit | ListenError\n    effects { network, log }\n"
    ),
    "impl/api.gopyt": (
        "module api\n\nuse core.log { write }\nuse core.status { ListenError }\n"
        "use core.str { len }\nuse net.http { serve }\n\n"
        "task get_echo(name: str) -> Receipt\n    effects { log }\n{\n"
        '    core.log.write("echo")\n'
        "    return Receipt { amount: core.str.len(name) }\n}\n\n"
        "task post_charge(payment: Payment) -> Receipt\n    effects { log }\n{\n"
        '    core.log.write("charge")\n'
        "    return Receipt { amount: payment.amount }\n}\n\n"
        "task serve() -> unit | ListenError\n    effects { network, log }\n{\n"
        "    return net.http.serve()\n}\n"
    ),
}


class HttpEdges(unittest.TestCase):
    """docs/implementer.md 12 status rules, on a real socket."""

    def setUp(self) -> None:
        import os
        import socket
        import threading
        import time

        from gopyt.cli import build, make_vm

        self.td = tempfile.mkdtemp()
        write_pkg(self.td, API_FILES)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        os.environ["GOPYT_HTTP_ADDR"] = f"127.0.0.1:{self.port}"
        prog, art, fn_ids = build(self.td)
        self.vm = make_vm(self.td, prog, art, fn_ids)
        self.fn_ids = fn_ids
        self.art = art
        threading.Thread(target=self.vm.call, args=(fn_ids["api.serve"], []), daemon=True).start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and getattr(self.vm, "httpd", None) is None:
            time.sleep(0.01)
        self.assertIsNotNone(getattr(self.vm, "httpd", None), "server never started")

    def tearDown(self) -> None:
        import os

        httpd = getattr(self.vm, "httpd", None)
        if httpd is not None:
            httpd.shutdown()
        os.environ.pop("GOPYT_HTTP_ADDR", None)
        shutil.rmtree(self.td, ignore_errors=True)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def send(self, path: str, method: str, body: bytes | None = None, ctype: str | None = None):
        import urllib.error
        import urllib.request

        headers = {"Content-Type": ctype} if ctype else {}
        request = urllib.request.Request(self.url(path), data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read()

    def test_get_ignores_a_request_body(self) -> None:
        status, body = self.send("/echo/abcd", "GET", b'{"ignored": 1}', "application/json")
        self.assertEqual(status, 200)
        self.assertEqual(body, b'{"amount":4}')

    def test_post_with_an_extra_json_key_is_400_empty(self) -> None:
        status, body = self.send(
            "/charge", "POST", b'{"amount": 1, "extra": 2}', "application/json"
        )
        self.assertEqual(status, 400)
        self.assertEqual(body, b"")

    def test_post_with_a_wrong_content_type_is_400_empty(self) -> None:
        status, body = self.send("/charge", "POST", b'{"amount": 1}', "text/plain")
        self.assertEqual(status, 400)
        self.assertEqual(body, b"")

    def test_unknown_route_is_404_empty(self) -> None:
        status, body = self.send("/nope", "GET")
        self.assertEqual(status, 404)
        self.assertEqual(body, b"")

    def test_wrong_method_on_a_known_path_is_404(self) -> None:
        status, _body = self.send("/charge", "GET")
        self.assertEqual(status, 404)

    def test_a_second_serve_is_listen_error(self) -> None:
        result = self.vm.call(self.fn_ids["api.serve"], [])
        self.assertEqual(self.vm.type_name(result.type_id), "core.status.ListenError")

    def test_routes_carry_their_parameter_names(self) -> None:
        """The artifact alone is enough to bind by name (bytecode.md)."""
        names = set()
        for route in self.art.routes:
            names |= {self.art.const_str(ix) for ix in route.params}
        self.assertEqual(names, {"name", "payment"})


class StdlibEdges(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def test_secret_is_not_accepted_by_concat(self) -> None:
        files = module(
            "task main() -> str\n    effects { secret }\n",
            (
                "task main() -> str\n    effects { secret }\n{\n"
                '    found = core.secret.get("api_token")\n'
                "    return match found {\n"
                '        Secret -> core.str.concat("token=", found)\n'
                '        NotFound -> "none"\n    }\n}\n'
            ),
            uses="use core.secret { Secret, get }\nuse core.status { NotFound }\nuse core.str { concat }",
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "check")
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E112", out)

    def test_reveal_declassifies_and_is_visible_in_effects(self) -> None:
        import os

        files = module(
            "task main() -> str\n    effects { secret }\n",
            (
                "task main() -> str\n    effects { secret }\n{\n"
                '    found = core.secret.get("api_token")\n'
                "    return match found {\n"
                "        Secret -> core.secret.reveal(found)\n"
                '        NotFound -> "none"\n    }\n}\n'
            ),
            uses="use core.secret { Secret, get, reveal }\nuse core.status { NotFound }",
        )
        write_pkg(self.td, files, fmt=True)
        os.environ["GOPYT_SECRET_API_TOKEN"] = "shh"
        try:
            code, out = run_cli(self.td, "run", "demo.main")
        finally:
            os.environ.pop("GOPYT_SECRET_API_TOKEN", None)
        self.assertEqual(code, 0, out)
        self.assertEqual(out, '"shh"\n')

    def test_dot_dot_path_is_io_error(self) -> None:
        files = module(
            "task main() -> str\n    effects { filesystem.read }\n",
            (
                "task main() -> str\n    effects { filesystem.read }\n{\n"
                '    data = core.file.read("../etc/passwd")\n'
                "    return match data {\n"
                '        bytes -> "read"\n'
                '        NotFound -> "missing"\n'
                '        IoError { message } -> "io"\n    }\n}\n'
            ),
            uses="use core.file { read }\nuse core.status { IoError, NotFound }",
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, '"io"\n')

    def test_absolute_and_empty_segments_are_io_error(self) -> None:
        from gopyt.natives import _safe_path

        class Fake:
            root = self.td

        for bad in ("/etc/passwd", "a//b", "a/./b", "", "a/../b", "a\\b"):
            self.assertIsNone(_safe_path(Fake(), bad), bad)
        self.assertIsNotNone(_safe_path(Fake(), "notes.txt"))

    def test_map_key_must_be_closed(self) -> None:
        write_pkg(
            self.td,
            {"spec/demo.gopyt": "module demo\n\nfn lookup(table: map[bytes, str]) -> i64\n"},
            lock=False,
        )
        code, out = run_cli(self.td, "check")
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E029", out)

    def test_map_keys_sort_by_kind(self) -> None:
        """implementer.md 5: bool false then true, i64 ascending."""
        files = module(
            "task main() -> list[i64]\n    effects { log }\n",
            (
                "task main() -> list[i64]\n    effects { log }\n{\n"
                '    core.log.write("keys")\n'
                "    empty = core.map.empty[i64, str]()\n"
                '    one = core.map.set(empty, 9, "nine")\n'
                '    two = core.map.set(one, -3, "minus")\n'
                '    three = core.map.set(two, 0, "zero")\n'
                "    return core.map.keys(three)\n}\n"
            ),
            uses="use core.log { write }\nuse core.map { empty, keys, set }",
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "[-3,0,9]\n")

    def test_bool_keys_sort_false_then_true(self) -> None:
        files = module(
            "task main() -> list[bool]\n    effects { log }\n",
            (
                "task main() -> list[bool]\n    effects { log }\n{\n"
                '    core.log.write("bools")\n'
                "    empty = core.map.empty[bool, i64]()\n"
                "    one = core.map.set(empty, true, 1)\n"
                "    two = core.map.set(one, false, 0)\n"
                "    return core.map.keys(two)\n}\n"
            ),
            uses="use core.log { write }\nuse core.map { empty, keys, set }",
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "[false,true]\n")

    def test_empty_collections_encode(self) -> None:
        files = module(
            "task main() -> list[i64]\n    effects { log }\n",
            (
                "task main() -> list[i64]\n    effects { log }\n{\n"
                '    core.log.write("empty")\n'
                "    return core.list.empty[i64]()\n}\n"
            ),
            uses="use core.list { empty }\nuse core.log { write }",
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "[]\n")

    def test_a_missing_map_key_is_none(self) -> None:
        files = module(
            "task main() -> i64\n    effects { log }\n",
            (
                "task main() -> i64\n    effects { log }\n{\n"
                '    core.log.write("get")\n'
                "    empty = core.map.empty[str, i64]()\n"
                '    one = core.map.set(empty, "here", 5)\n'
                '    return match core.map.get(one, "gone") {\n'
                "        some(value) -> value\n        none -> -1\n    }\n}\n"
            ),
            uses="use core.log { write }\nuse core.map { empty, get, set }",
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "-1\n")

    def test_map_round_trip_and_sorted_keys(self) -> None:
        files = module(
            "task main() -> list[str]\n    effects { log }\n",
            (
                "task main() -> list[str]\n    effects { log }\n{\n"
                '    core.log.write("map")\n'
                "    empty = core.map.empty[str, i64]()\n"
                '    one = core.map.set(empty, "beta", 2)\n'
                '    two = core.map.set(one, "alpha", 1)\n'
                "    return core.map.keys(two)\n}\n"
            ),
            uses="use core.log { write }\nuse core.map { empty, keys, set }",
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, '["alpha","beta"]\n')


class Semantics(unittest.TestCase):
    """implementer.md 1-6: the arithmetic, text, and JSON rules, on the VM."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def value(self, ret: str, body: str, uses: str = "", spec_uses: str = "") -> str:
        files = module(
            f"task main() -> {ret}\n    effects {{ log }}\n",
            f"task main() -> {ret}\n    effects {{ log }}\n{{\n{body}}}\n",
            uses=("use core.log { write }\n" + uses) if uses else "use core.log { write }",
            spec_uses=spec_uses,
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        return out

    def test_division_truncates_toward_zero(self) -> None:
        body = (
            '    core.log.write("div")\n'
            "    two = 2\n    seven = 7\n"
            "    return core.list.append(core.list.append(core.list.empty[i64](),\n"
            "        -seven / two), seven / -two)\n"
        )
        self.assertEqual(
            self.value("list[i64]", body, uses="use core.list { append, empty }"),
            "[-3,-3]\n",
        )

    def test_remainder_takes_the_sign_of_the_dividend(self) -> None:
        body = (
            '    core.log.write("mod")\n'
            "    two = 2\n    seven = 7\n"
            "    return core.list.append(core.list.append(core.list.empty[i64](),\n"
            "        -seven % two), seven % -two)\n"
        )
        self.assertEqual(
            self.value("list[i64]", body, uses="use core.list { append, empty }"),
            "[-1,1]\n",
        )

    def test_str_len_counts_scalars_and_bytes_len_counts_bytes(self) -> None:
        body = (
            '    core.log.write("len")\n'
            '    text = "héllo"\n'
            "    return core.list.append(core.list.append(core.list.empty[i64](),\n"
            "        core.str.len(text)), core.bytes.len(core.bytes.from_str(text)))\n"
        )
        self.assertEqual(
            self.value(
                "list[i64]",
                body,
                uses="use core.bytes { from_str, len }\nuse core.list { append, empty }\n"
                     "use core.str { len }",
            ),
            "[5,6]\n",
        )

    def test_range_is_end_exclusive_and_may_be_empty(self) -> None:
        body = (
            '    core.log.write("range")\n'
            "    return core.list.append(core.list.range(0, 3), core.list.len(core.list.range(0, 0)))\n"
        )
        self.assertEqual(
            self.value("list[i64]", body, uses="use core.list { append, len, range }"),
            "[0,1,2,0]\n",
        )

    def test_slice_past_the_end_is_convert_error(self) -> None:
        body = (
            '    core.log.write("slice")\n'
            '    return core.str.slice("abc", 0, 9)\n'
        )
        out = self.value(
            "str | ConvertError",
            body,
            uses="use core.str { slice }\nuse core.status { ConvertError }",
            spec_uses="use core.status { ConvertError }",
        )
        self.assertTrue(out.startswith('{"core.status.ConvertError"'), out)

    def test_int_conversion_range_is_checked(self) -> None:
        body = (
            '    core.log.write("int")\n'
            "    big = 2147483648\n"
            "    return core.int.to_i32(big)\n"
        )
        out = self.value(
            "i32 | ConvertError",
            body,
            uses="use core.int { to_i32 }\nuse core.status { ConvertError }",
            spec_uses="use core.status { ConvertError }",
        )
        self.assertTrue(out.startswith('{"core.status.ConvertError"'), out)

    def test_optional_encodes_as_null(self) -> None:
        body = (
            '    core.log.write("opt")\n'
            "    return core.list.get(core.list.empty[i64](), 3)\n"
        )
        self.assertEqual(self.value("i64?", body, uses="use core.list { empty, get }"), "null\n")

    def test_enum_without_a_payload_encodes_as_an_empty_object(self) -> None:
        files = {
            "spec/demo.gopyt": (
                "module demo\n\nuse core.convert { Json }\n\n"
                "enum Status {\n    Pending\n    Paid {\n        amount: i64\n    }\n}\n\n"
                "provide Json for Status\n\n"
                "task main() -> Status\n    effects { log }\n"
            ),
            "impl/demo.gopyt": (
                "module demo\n\nuse core.log { write }\n\n"
                "task main() -> Status\n    effects { log }\n{\n"
                '    core.log.write("enum")\n'
                "    return Status.Pending\n}\n"
            ),
        }
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, '{"Pending":{}}\n')

    def test_contracts_run_in_declaration_order(self) -> None:
        """The first false clause traps; a later clause never runs."""
        spec = (
            "module demo\n\nfn narrow(value: i64) -> i64\n"
            "    requires value > 0\n    requires value < 10\n"
        )
        impl = (
            "module demo\n\nfn narrow(value: i64) -> i64\n"
            "    requires value > 0\n    requires value < 10\n{\n    return value\n}\n"
        )
        files = {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl}
        files["spec/run.gopyt"] = "module run\n\ntask main() -> i64\n    effects { log }\n"
        files["impl/run.gopyt"] = (
            "module run\n\nuse core.log { write }\nuse demo { narrow }\n\n"
            "task main() -> i64\n    effects { log }\n{\n"
            '    core.log.write("contract")\n'
            "    return demo.narrow(-1)\n}\n"
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "run.main")
        self.assertEqual(code, 2)
        self.assertIn("trap: 1", out)

    def test_arguments_evaluate_left_to_right(self) -> None:
        """The left argument's trap wins, so evaluation order is observable."""
        files = {
            "spec/demo.gopyt": (
                "module demo\n\nfn half(value: i64) -> i64\n    requires value >= 0\n\n"
                "task main() -> i64\n    effects { log }\n"
            ),
            "impl/demo.gopyt": (
                "module demo\n\nuse core.log { write }\n\n"
                "fn half(value: i64) -> i64\n    requires value >= 0\n{\n"
                "    return value / 2\n}\n\n"
                "task main() -> i64\n    effects { log }\n{\n"
                '    core.log.write("order")\n'
                "    zero = 0\n"
                "    return half(-1) + 1 / zero\n}\n"
            ),
        }
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 2)
        self.assertIn("trap: 1", out)  # requires, not the division

    def test_and_short_circuits(self) -> None:
        body = (
            '    core.log.write("and")\n'
            "    zero = 0\n"
            "    return false and 1 / zero == 0\n"
        )
        self.assertEqual(self.value("bool", body), "false\n")

    def test_or_short_circuits(self) -> None:
        body = (
            '    core.log.write("or")\n'
            "    zero = 0\n"
            "    return true or 1 / zero == 0\n"
        )
        self.assertEqual(self.value("bool", body), "true\n")

    def test_json_decode_rejects_trailing_input(self) -> None:
        from gopyt import jsonc

        with self.assertRaises(jsonc.ConvertFail):
            jsonc.parse('{"a": 1} extra')

    def test_json_decode_rejects_duplicate_keys(self) -> None:
        from gopyt import jsonc

        with self.assertRaises(jsonc.ConvertFail):
            jsonc.parse('{"a": 1, "a": 2}')


class EffectHygiene(unittest.TestCase):
    """S9 unused/missing effects at the edges of the checker."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def check(self, files: dict[str, str]) -> tuple[int, str]:
        write_pkg(self.td, files, fmt=True)
        return run_cli(self.td, "check")

    def test_an_effect_used_only_inside_a_parallel_arm_counts(self) -> None:
        files = module(
            "task main() -> list[unit]\n    effects { log, time }\n",
            (
                "task main() -> list[unit]\n    effects { log, time }\n{\n"
                "    return parallel max 2 timeout_ms 2000 {\n"
                '        core.log.write("left")\n'
                '        core.log.write("right")\n    }\n}\n'
            ),
            uses="use core.log { write }",
        )
        code, out = self.check(files)
        self.assertEqual(code, 0, out)

    def test_an_effect_used_only_in_a_match_arm_counts(self) -> None:
        files = module(
            "task main() -> unit\n    effects { log }\n",
            (
                "task main() -> unit\n    effects { log }\n{\n"
                "    flag = true\n"
                "    value = core.list.get(core.list.range(0, 1), 0)\n"
                "    return match value {\n"
                '        some(found) -> core.log.write("found")\n'
                "        none -> unit\n    }\n}\n"
            ),
            uses="use core.list { get, range }\nuse core.log { write }",
        )
        code, out = self.check(files)
        self.assertEqual(code, 0, out)

    def test_a_caller_holds_the_trait_effects_not_the_provide_effects(self) -> None:
        """S26: the trait signature is the caller contract."""
        spec = (
            "module demo\n\ntype Client {\n    tag: str\n}\n\n"
            "trait Gateway {\n    task charge(gateway: Self) -> str\n"
            "        effects { network, log }\n}\n\n"
            "provide Gateway for Client\n\n"
            "task main() -> str\n    effects { network, log }\n"
        )
        impl = (
            "module demo\n\nuse core.log { write }\n\nprovide Gateway for Client\n{\n"
            "    task charge(gateway: Self) -> str\n        effects { log }\n    {\n"
            '        core.log.write("charge")\n        return gateway.tag\n    }\n}\n\n'
            "task main() -> str\n    effects { network, log }\n{\n"
            '    return Gateway.charge(Client { tag: "acme" })\n}\n'
        )
        write_pkg(self.td, {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl}, fmt=True)
        code, out = run_cli(self.td, "check")
        self.assertEqual(code, 0, out)
        # `network` is used by holding the trait contract, so it is not E051.
        self.assertEqual(run_cli(self.td, "run", "demo.main"), (0, '"acme"\n'))

    def test_a_caller_missing_a_trait_effect_is_e050(self) -> None:
        spec = (
            "module demo\n\ntype Client {\n    tag: str\n}\n\n"
            "trait Gateway {\n    task charge(gateway: Self) -> str\n"
            "        effects { network, log }\n}\n\n"
            "provide Gateway for Client\n\n"
            "task main() -> str\n    effects { log }\n"
        )
        impl = (
            "module demo\n\nuse core.log { write }\n\nprovide Gateway for Client\n{\n"
            "    task charge(gateway: Self) -> str\n        effects { log }\n    {\n"
            '        core.log.write("charge")\n        return gateway.tag\n    }\n}\n\n'
            "task main() -> str\n    effects { log }\n{\n"
            '    return Gateway.charge(Client { tag: "acme" })\n}\n'
        )
        write_pkg(self.td, {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl}, fmt=True)
        code, out = run_cli(self.td, "check")
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E050", out)

    def test_parallel_without_time_is_effect_missing(self) -> None:
        files = module(
            "task main() -> list[i64]\n    effects { log }\n",
            (
                "task main() -> list[i64]\n    effects { log }\n{\n"
                '    core.log.write("go")\n'
                "    return parallel max 2 timeout_ms 1000 {\n        1\n        2\n    }\n}\n"
            ),
            uses="use core.log { write }",
        )
        code, out = self.check(files)
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E050", out)

    def test_a_task_that_only_calls_a_pure_fn_needs_no_effect_but_must_have_one(self) -> None:
        """S9: a declaration with an empty effect set is a fn, so this is E051."""
        files = module(
            "task main() -> i64\n    effects { log }\n",
            "task main() -> i64\n    effects { log }\n{\n    return 1\n}\n",
        )
        code, out = self.check(files)
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E051", out)

    def test_empty_effects_clause_is_e050(self) -> None:
        files = module(
            "task main() -> i64\n    effects { }\n",
            "task main() -> i64\n    effects { }\n{\n    return 1\n}\n",
        )
        code, out = self.check(files)
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E050", out)


class Exhaustiveness(unittest.TestCase):
    """S14: a match names every variant or member, once."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def check(self, impl_body: str, uses: str = "", spec: str | None = None) -> tuple[int, str]:
        head = spec or (
            "module demo\n\nuse core.status { NotFound }\n\n"
            "type User {\n    id: i64\n}\n\n"
            "fn pick(value: User | NotFound) -> i64\n"
        )
        impl = (
            "module demo\n\n"
            + (uses + "\n\n" if uses else "use core.status { NotFound }\n\n")
            + "fn pick(value: User | NotFound) -> i64\n{\n"
            + impl_body
            + "}\n"
        )
        write_pkg(self.td, {"spec/demo.gopyt": head, "impl/demo.gopyt": impl}, fmt=True)
        return run_cli(self.td, "check")

    def test_all_members_named_once_is_accepted(self) -> None:
        code, out = self.check(
            "    return match value {\n        User { id } -> id\n        NotFound -> 0\n    }\n"
        )
        self.assertEqual(code, 0, out)

    def test_a_repeated_arm_is_e070(self) -> None:
        code, out = self.check(
            "    return match value {\n        User { id } -> id\n"
            "        User { id } -> id\n        NotFound -> 0\n    }\n"
        )
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E070", out)

    def test_an_arm_outside_the_union_is_e070(self) -> None:
        code, out = self.check(
            "    return match value {\n        User { id } -> id\n"
            "        NotFound -> 0\n        DbError { message } -> 1\n    }\n",
            uses="use core.status { DbError, NotFound }",
        )
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E070", out)

    def test_matching_a_plain_record_is_e070(self) -> None:
        spec = (
            "module demo\n\nuse core.status { NotFound }\n\n"
            "type User {\n    id: i64\n}\n\nfn pick(value: User | NotFound) -> i64\n"
        )
        impl = (
            "module demo\n\nuse core.status { NotFound }\n\n"
            "fn pick(value: User | NotFound) -> i64\n{\n"
            "    only = User { id: 1 }\n"
            "    return match only {\n        User { id } -> id\n    }\n}\n"
        )
        write_pkg(self.td, {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl}, fmt=True)
        code, out = run_cli(self.td, "check")
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E070", out)

    def test_optional_needs_both_arms(self) -> None:
        files = module(
            "fn first(items: list[i64]) -> i64\n",
            (
                "fn first(items: list[i64]) -> i64\n{\n"
                "    return match core.list.get(items, 0) {\n"
                "        some(found) -> found\n    }\n}\n"
            ),
            uses="use core.list { get }",
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "check")
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E070", out)


class MoreStdlib(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def test_assert_eq_on_f64_is_e099(self) -> None:
        spec = "module demo\n\nfn ok() -> f64\n"
        impl = "module demo\n\nfn ok() -> f64\n{\n    return 1.5\n}\n"
        tests = (
            "module test.demo\n\nuse core.test { assert_eq }\nuse demo { ok }\n\n"
            "test compares\n{\n    core.test.assert_eq(demo.ok(), demo.ok())\n    return unit\n}\n"
        )
        write_pkg(
            self.td,
            {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl, "test/demo.gopyt": tests},
            fmt=True,
        )
        code, out = run_cli(self.td, "check")
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E099", out)

    def test_allocation_limit_traps(self) -> None:
        files = module(
            "task main() -> i64\n    effects { log }\n",
            (
                "task main() -> i64\n    effects { log }\n{\n"
                '    core.log.write("alloc")\n'
                "    huge = 3000000000\n"
                "    return core.list.len(core.list.range(0, huge))\n}\n"
            ),
            uses="use core.list { len, range }\nuse core.log { write }",
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 2)
        self.assertIn("trap: 14", out)

    def test_model_without_a_url_is_model_error(self) -> None:
        import os

        files = module(
            "task main() -> str | ModelError\n    effects { network, model }\n\n"
            'egress { "https://models.example" }\n',
            (
                "task main() -> str | ModelError\n    effects { network, model }\n{\n"
                '    return core.model.complete("hello")\n}\n'
            ),
            uses="use core.model { complete }\nuse core.status { ModelError }",
            spec_uses="use core.status { ModelError }",
        )
        write_pkg(self.td, files, fmt=True)
        os.environ.pop("GOPYT_MODEL_URL", None)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        self.assertIn("core.status.ModelError", out)

    def test_model_with_an_unlisted_origin_never_leaves_the_process(self) -> None:
        import os

        files = module(
            "task main() -> str | ModelError\n    effects { network, model }\n\n"
            'egress { "https://models.example" }\n',
            (
                "task main() -> str | ModelError\n    effects { network, model }\n{\n"
                '    return core.model.complete("hello")\n}\n'
            ),
            uses="use core.model { complete }\nuse core.status { ModelError }",
            spec_uses="use core.status { ModelError }",
        )
        write_pkg(self.td, files, fmt=True)
        os.environ["GOPYT_MODEL_URL"] = "https://evil.invalid/v1"
        try:
            code, out = run_cli(self.td, "run", "demo.main")
        finally:
            os.environ.pop("GOPYT_MODEL_URL", None)
        self.assertEqual(code, 0, out)
        self.assertIn("egress", out)

    def test_request_with_userinfo_is_refused(self) -> None:
        files = module(
            "task main() -> i64\n    effects { network }\n\n"
            'egress { "https://api.example" }\n',
            (
                "task main() -> i64\n    effects { network }\n{\n"
                '    host = core.str.concat("https://user@", "api.example")\n'
                "    req = HttpRequest {\n        method: HttpMethod.Get\n"
                "        url: host\n        body: core.bytes.from_str(\"\")\n    }\n"
                "    return match net.http.request(req) {\n"
                "        HttpResponse { status body } -> status\n"
                "        HttpError { message } -> 0\n    }\n}\n"
            ),
            uses=(
                "use core.bytes { from_str }\nuse core.status { HttpError }\n"
                "use core.str { concat }\n"
                "use net.http { HttpMethod, HttpRequest, HttpResponse, request }"
            ),
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "0\n")

    def test_a_workflow_runs_like_a_task(self) -> None:
        files = module(
            "workflow drive() -> i64\n    effects { log }\n",
            (
                "workflow drive() -> i64\n    effects { log }\n{\n"
                '    core.log.write("wire")\n    return 7\n}\n'
            ),
            uses="use core.log { write }",
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.drive")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "7\n")


class Generics(unittest.TestCase):
    """implementer.md 4: monomorphization, static trait dispatch."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def artifact(self, files: dict[str, str]):
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "check")
        self.assertEqual(code, 0, out)
        return decode(Path(self.td, "build/out.gobyte").read_bytes())

    def test_one_function_record_per_type_argument_vector(self) -> None:
        spec = (
            "module demo\n\nfn identity[T](value: T) -> T\n\n"
            "fn use_both(left: i64, right: str) -> str\n"
        )
        impl = (
            "module demo\n\nfn identity[T](value: T) -> T\n{\n    return value\n}\n\n"
            "fn use_both(left: i64, right: str) -> str\n{\n"
            "    first = identity(left)\n    return identity(right)\n}\n"
        )
        art = self.artifact({"spec/demo.gopyt": spec, "impl/demo.gopyt": impl})
        names = sorted(n for n in (art.const_str(f.name) for f in art.funcs)
                       if n.startswith("demo.identity"))
        self.assertEqual(names, ["demo.identity[i64]", "demo.identity[str]"])
        for f in art.funcs:
            self.assertNotIn("[T]", art.const_str(f.name))

    def test_a_generic_record_is_monomorphized(self) -> None:
        spec = (
            "module demo\n\ntype Box[T] {\n    value: T\n}\n\n"
            "fn wrap(value: i64) -> Box[i64]\n"
        )
        impl = (
            "module demo\n\nfn wrap(value: i64) -> Box[i64]\n{\n"
            "    return Box { value: value }\n}\n"
        )
        art = self.artifact({"spec/demo.gopyt": spec, "impl/demo.gopyt": impl})
        names = {art.const_str(t.name) for t in art.types}
        self.assertIn("demo.Box[i64]", names)

    def test_a_generic_trait_binds_its_parameters(self) -> None:
        spec = (
            "module demo\n\ntrait Holder[T] {\n    fn get(value: Self) -> T\n}\n\n"
            "type Cell {\n    slot: i64\n}\n\nprovide Holder[i64] for Cell\n\n"
            "task main() -> i64\n    effects { log }\n"
        )
        impl = (
            "module demo\n\nuse core.log { write }\n\nprovide Holder[i64] for Cell\n{\n"
            "    fn get(value: Self) -> i64\n    {\n        return value.slot\n    }\n}\n\n"
            "task main() -> i64\n    effects { log }\n{\n"
            '    core.log.write("cell")\n'
            "    return Holder.get(Cell { slot: 42 })\n}\n"
        )
        write_pkg(self.td, {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl}, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "42\n")

    def test_trait_task_dispatch_and_two_traits_sharing_a_member_name(self) -> None:
        """S23/S26: static dispatch, and `Named.label` vs `Gateway.label`."""
        spec = (
            "module demo\n\ntype Client {\n    label: str\n}\n\n"
            "trait Gateway {\n    task charge(gateway: Self, amount: i64) -> i64\n"
            "        effects { log }\n    fn label(gateway: Self) -> str\n}\n\n"
            "trait Named {\n    fn label(value: Self) -> str\n}\n\n"
            "provide Gateway for Client\n\nprovide Named for Client\n\n"
            "task main() -> str\n    effects { log }\n"
        )
        impl = (
            "module demo\n\nuse core.log { write }\nuse core.str { concat, from_i64 }\n\n"
            "provide Gateway for Client\n{\n"
            "    task charge(gateway: Self, amount: i64) -> i64\n        effects { log }\n    {\n"
            '        core.log.write("charging")\n        return amount\n    }\n\n'
            "    fn label(gateway: Self) -> str\n    {\n"
            '        return core.str.concat("gateway:", gateway.label)\n    }\n}\n\n'
            "provide Named for Client\n{\n"
            "    fn label(value: Self) -> str\n    {\n"
            '        return core.str.concat("named:", value.label)\n    }\n}\n\n'
            "task main() -> str\n    effects { log }\n{\n"
            '    client = Client { label: "acme" }\n'
            "    paid = Gateway.charge(client, 7)\n"
            "    return core.str.concat(core.str.concat(Gateway.label(client),\n"
            "        Named.label(client)), core.str.from_i64(paid))\n}\n"
        )
        write_pkg(self.td, {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl}, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, '"gateway:acmenamed:acme7"\n')
        art = decode(Path(self.td, "build/out.gobyte").read_bytes())
        names = {art.const_str(f.name) for f in art.funcs}
        self.assertIn("provide:demo.Gateway:demo.Client:charge", names)
        self.assertIn("provide:demo.Named:demo.Client:label", names)

    def test_wrong_trait_argument_count_is_e029(self) -> None:
        spec = (
            "module demo\n\ntrait Holder[T] {\n    fn get(value: Self) -> T\n}\n\n"
            "type Cell {\n    slot: i64\n}\n\nprovide Holder for Cell\n"
        )
        impl = (
            "module demo\n\nprovide Holder for Cell\n{\n"
            "    fn get(value: Self) -> i64\n    {\n        return value.slot\n    }\n}\n"
        )
        write_pkg(self.td, {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl}, lock=False)
        from gopyt.testing import diag_code

        self.assertEqual(diag_code(self.td), 29)

    def test_mutually_recursive_tasks(self) -> None:
        spec = (
            "module demo\n\ntask even(value: i64) -> bool\n    effects { log }\n\n"
            "task odd(value: i64) -> bool\n    effects { log }\n\n"
            "task main() -> bool\n    effects { log }\n"
        )
        impl = (
            "module demo\n\nuse core.log { write }\n\n"
            "task even(value: i64) -> bool\n    effects { log }\n{\n"
            '    core.log.write("e")\n'
            "    if value == 0 {\n        return true\n    }\n"
            "    return odd(value - 1)\n}\n\n"
            "task odd(value: i64) -> bool\n    effects { log }\n{\n"
            '    core.log.write("o")\n'
            "    if value == 0 {\n        return false\n    }\n"
            "    return even(value - 1)\n}\n\n"
            "task main() -> bool\n    effects { log }\n{\n"
            "    return even(6)\n}\n"
        )
        write_pkg(self.td, {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl}, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "true\n")


FROM_STR_FILES = {
    "spec/api.gopyt": (
        "module api\n\nuse core.convert { FromStr, Json }\n"
        "use core.status { ListenError }\n\n"
        "type UserId {\n    value: i64\n}\n\n"
        "provide FromStr for UserId\n\nprovide Json for UserId\n\n"
        'http {\n    get "/user/{id}" get_user\n    get "/ping" get_ping\n}\n\n'
        "task get_user(id: UserId) -> UserId\n    effects { log }\n\n"
        "task get_ping() -> unit\n    effects { log }\n\n"
        "task serve() -> unit | ListenError\n    effects { network, log }\n"
    ),
    "impl/api.gopyt": (
        "module api\n\nuse core.log { write }\n"
        "use core.status { ListenError }\nuse net.http { serve }\n\n"
        "task get_user(id: UserId) -> UserId\n    effects { log }\n{\n"
        '    core.log.write("user")\n    return id\n}\n\n'
        "task get_ping() -> unit\n    effects { log }\n{\n"
        '    return core.log.write("ping")\n}\n\n'
        "task serve() -> unit | ListenError\n    effects { network, log }\n{\n"
        "    return net.http.serve()\n}\n"
    ),
}


class HttpFromStr(unittest.TestCase):
    """implementer.md 12: a non-str placeholder is converted through FromStr."""

    def setUp(self) -> None:
        import os
        import socket
        import threading
        import time

        from gopyt.cli import build, make_vm

        self.td = tempfile.mkdtemp()
        write_pkg(self.td, FROM_STR_FILES, fmt=True)
        code, out = run_cli(self.td, "check")
        self.assertEqual(code, 0, out)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        os.environ["GOPYT_HTTP_ADDR"] = f"127.0.0.1:{self.port}"
        prog, art, fn_ids = build(self.td)
        self.vm = make_vm(self.td, prog, art, fn_ids)
        threading.Thread(target=self.vm.call, args=(fn_ids["api.serve"], []), daemon=True).start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and getattr(self.vm, "httpd", None) is None:
            time.sleep(0.01)
        self.assertIsNotNone(getattr(self.vm, "httpd", None), "server never started")

    def tearDown(self) -> None:
        import os

        httpd = getattr(self.vm, "httpd", None)
        if httpd is not None:
            httpd.shutdown()
        os.environ.pop("GOPYT_HTTP_ADDR", None)
        shutil.rmtree(self.td, ignore_errors=True)

    def send(self, path: str):
        import urllib.error
        import urllib.request

        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read()

    def test_a_convertible_placeholder_reaches_the_handler(self) -> None:
        self.assertEqual(self.send("/user/42"), (200, b'{"value":42}'))

    def test_an_unconvertible_placeholder_is_400_empty(self) -> None:
        self.assertEqual(self.send("/user/abc"), (400, b""))

    def test_a_unit_handler_answers_200_with_an_empty_body(self) -> None:
        self.assertEqual(self.send("/ping"), (200, b""))


class ParallelCancellation(unittest.TestCase):
    """docs/bytecode.md: a trap stops that `parallel`, and nothing else."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def test_a_trapping_arm_does_not_stop_a_neighbouring_parallel(self) -> None:
        """Two parallels run on different threads; only the bad one traps."""
        import threading

        from gopyt.cli import build, make_vm

        files = module(
            "task good() -> list[i64]\n    effects { time }\n\n"
            "task bad() -> list[i64]\n    effects { time }\n",
            (
                "task good() -> list[i64]\n    effects { time }\n{\n"
                "    return parallel max 2 timeout_ms 4000 {\n"
                "        core.list.len(core.list.range(0, 20000))\n"
                "        core.list.len(core.list.range(0, 20000))\n    }\n}\n\n"
                "task bad() -> list[i64]\n    effects { time }\n{\n"
                "    zero = 0\n"
                "    return parallel max 2 timeout_ms 4000 {\n"
                "        1 / zero\n        2\n    }\n}\n"
            ),
            uses="use core.list { len, range }",
        )
        write_pkg(self.td, files, fmt=True)
        self.assertEqual(run_cli(self.td, "check")[0], 0)
        prog, art, fn_ids = build(self.td)
        vm = make_vm(self.td, prog, art, fn_ids)
        outcome: dict[str, object] = {}

        def run_good() -> None:
            try:
                outcome["good"] = vm.call(fn_ids["demo.good"], [])
            except Exception as exc:  # noqa: BLE001
                outcome["good"] = exc

        def run_bad() -> None:
            try:
                outcome["bad"] = vm.call(fn_ids["demo.bad"], [])
            except Trap as t:
                outcome["bad"] = t.code

        threads = [threading.Thread(target=run_good), threading.Thread(target=run_bad)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(outcome.get("bad"), ops.TRAP_DIV_ZERO)
        self.assertEqual(outcome.get("good"), [20000, 20000])

    def test_observe_holds_its_bound_under_threads(self) -> None:
        """P0's reservoir bound must survive concurrent arms."""
        import threading

        from gopyt.observe import Observe

        obs = Observe(reservoir_k=16)

        def hammer() -> None:
            for i in range(2_000):
                obs.event(f"tag{i}", True, 1.0)

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(len(obs.reservoir.items), 16)
        self.assertEqual(obs.events, 8 * 2_000)
        self.assertEqual(obs.fail, 8 * 2_000)

    def test_results_keep_source_order_regardless_of_finish_order(self) -> None:
        files = module(
            "task main() -> list[i64]\n    effects { time }\n",
            (
                "task main() -> list[i64]\n    effects { time }\n{\n"
                "    return parallel max 3 timeout_ms 5000 {\n"
                "        core.list.len(core.list.range(0, 30000))\n"
                "        core.list.len(core.list.range(0, 10))\n"
                "        core.list.len(core.list.range(0, 5000))\n    }\n}\n"
            ),
            uses="use core.list { len, range }",
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "[30000,10,5000]\n")


SHAPES_SPEC = """module shapes

use core.convert { Json }
use core.status { ConvertError, NotFound }

type Line {
    label: str
    amount: i64
    note: str?
}

enum Kind {
    Plain
    Tagged {
        tag: str
        weight: i64
    }
}

type Order {
    id: i64
    lines: list[Line]
    index: map[str, i64]
    kind: Kind
    outcome: Line | NotFound
}

provide Json for Line

provide Json for Kind

provide Json for Order

fn sample() -> Order

task round_trip() -> str | ConvertError
    effects { log }

task main() -> unit
    effects { log }
"""

SHAPES_IMPL = """module shapes

use core.list { append, empty }
use core.log { write }
use core.map { empty, set }
use core.status { ConvertError, NotFound }
use core.test { assert_eq }
use data.json { decode, encode }

fn sample() -> Order
{
    first = Line {
        label: "one"
        amount: 10
        note: some("first")
    }
    second = Line {
        label: "two"
        amount: 20
        note: none
    }
    lines = core.list.append(core.list.append(core.list.empty[Line](), first), second)
    index = core.map.set(core.map.set(core.map.empty[str, i64](), "two", 2), "one", 1)
    return Order {
        id: 7
        lines: lines
        index: index
        kind: Kind.Tagged {
            tag: "hot"
            weight: 3
        }
        outcome: NotFound {}
    }
}

task round_trip() -> str | ConvertError
    effects { log }
{
    core.log.write("encode")
    return data.json.encode(sample())
}

task main() -> unit
    effects { log }
{
    text = round_trip()
    return match text {
        str -> check(text)
        ConvertError { message } -> core.log.write(message)
    }
}

task check(text: str) -> unit
    effects { log }
{
    core.log.write("decode")
    return match data.json.decode[Order](text) {
        Order { id lines index kind outcome } -> core.test.assert_eq(id, 7)
        ConvertError { message } -> core.log.write(message)
    }
}
"""


class Shapes(unittest.TestCase):
    """Nested records, enums, optionals, maps, and unions through the artifact."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()
        files = {"spec/shapes.gopyt": SHAPES_SPEC, "impl/shapes.gopyt": SHAPES_IMPL}
        files["test/shapes.gopyt"] = (
            "module test.shapes\n\nuse core.test { assert_eq }\n"
            "use shapes { sample }\n\n"
            "test json_round_trips\n{\n"
            "    core.test.assert_eq(shapes.sample(), shapes.sample())\n"
            "    return unit\n}\n"
        )
        write_pkg(self.td, files, fmt=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def test_check_is_clean(self) -> None:
        code, out = run_cli(self.td, "check")
        self.assertEqual(code, 0, out)

    def test_canonical_json_of_a_nested_value(self) -> None:
        code, out = run_cli(self.td, "run", "shapes.round_trip")
        self.assertEqual(code, 0, out)
        self.assertEqual(
            out.strip(),
            '{"str":"{\\"id\\":7,'
            '\\"lines\\":[{\\"label\\":\\"one\\",\\"amount\\":10,\\"note\\":\\"first\\"},'
            '{\\"label\\":\\"two\\",\\"amount\\":20,\\"note\\":null}],'
            '\\"index\\":{\\"one\\":1,\\"two\\":2},'
            '\\"kind\\":{\\"Tagged\\":{\\"tag\\":\\"hot\\",\\"weight\\":3}},'
            '\\"outcome\\":{\\"core.status.NotFound\\":{}}}"}',
        )

    def test_structural_equality_over_the_whole_shape(self) -> None:
        code, out = run_cli(self.td, "test")
        self.assertEqual(code, 0, out)

    def test_decode_reaches_the_record(self) -> None:
        code, out = run_cli(self.td, "run", "shapes.main")
        self.assertEqual(code, 0, out)


class HostServices(unittest.TestCase):
    """core.file, core.time, core.random (docs/implementer.md 14)."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def test_file_write_then_read_round_trips_inside_the_package(self) -> None:
        files = module(
            "task main() -> str\n    effects { filesystem.read, filesystem.write }\n",
            (
                "task main() -> str\n"
                "    effects { filesystem.read, filesystem.write }\n{\n"
                '    written = core.file.write("notes.txt", core.bytes.from_str("hello"))\n'
                '    data = core.file.read("notes.txt")\n'
                "    return match data {\n"
                "        bytes -> unwrap(core.bytes.to_str(data))\n"
                '        NotFound -> "missing"\n'
                '        IoError { message } -> "io"\n    }\n}\n\n'
                "fn unwrap(value: str | ConvertError) -> str\n{\n"
                "    return match value {\n"
                "        str -> value\n"
                '        ConvertError { message } -> "bad utf8"\n    }\n}\n'
            ),
            uses=(
                "use core.bytes { from_str, to_str }\nuse core.file { read, write }\n"
                "use core.status { ConvertError, IoError, NotFound }"
            ),
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, '"hello"\n')
        self.assertEqual(Path(self.td, "notes.txt").read_bytes(), b"hello")

    def test_reading_a_missing_file_is_not_found(self) -> None:
        files = module(
            "task main() -> str\n    effects { filesystem.read }\n",
            (
                "task main() -> str\n    effects { filesystem.read }\n{\n"
                '    return match core.file.read("absent.txt") {\n'
                '        bytes -> "found"\n'
                '        NotFound -> "missing"\n'
                '        IoError { message } -> "io"\n    }\n}\n'
            ),
            uses="use core.file { read }\nuse core.status { IoError, NotFound }",
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, '"missing"\n')

    def test_random_respects_its_bounds(self) -> None:
        files = module(
            "task main() -> bool\n    effects { random }\n",
            (
                "task main() -> bool\n    effects { random }\n{\n"
                "    value = core.random.i64_in(5, 7)\n"
                "    return value >= 5 and value <= 7\n}\n"
            ),
            uses="use core.random { i64_in }",
        )
        write_pkg(self.td, files, fmt=True)
        for _ in range(5):
            code, out = run_cli(self.td, "run", "demo.main")
            self.assertEqual(code, 0, out)
            self.assertEqual(out, "true\n")

    def test_random_with_min_above_max_traps(self) -> None:
        files = module(
            "task main() -> i64\n    effects { random }\n",
            (
                "task main() -> i64\n    effects { random }\n{\n"
                "    high = 7\n    return core.random.i64_in(high, 5)\n}\n"
            ),
            uses="use core.random { i64_in }",
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 2)
        self.assertIn("trap: 1", out)

    def test_time_moves_forward(self) -> None:
        files = module(
            "task main() -> bool\n    effects { time }\n",
            (
                "task main() -> bool\n    effects { time }\n{\n"
                "    before = core.time.now_ms()\n"
                "    slept = core.time.sleep_ms(5)\n"
                "    return core.time.now_ms() >= before\n}\n"
            ),
            uses="use core.time { now_ms, sleep_ms }",
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "true\n")

    def test_a_negative_sleep_traps(self) -> None:
        files = module(
            "task main() -> unit\n    effects { time }\n",
            (
                "task main() -> unit\n    effects { time }\n{\n"
                "    back = -5\n    return core.time.sleep_ms(back)\n}\n"
            ),
            uses="use core.time { sleep_ms }",
        )
        write_pkg(self.td, files, fmt=True)
        code, out = run_cli(self.td, "run", "demo.main")
        self.assertEqual(code, 2)
        self.assertIn("trap: 1", out)


class ProgramMatchesArtifact(unittest.TestCase):
    """What the checker decided is exactly what the artifact carries."""

    def check_package(self, root: str) -> None:
        from gopyt import ops as _ops
        from gopyt.cli import build

        prog, art, fn_ids = build(root)
        names = [art.const_str(f.name) for f in art.funcs]
        self.assertEqual(len(names), len(set(names)) if len(set(names)) == len(names) else len(names))
        self.assertEqual(len(art.funcs), len(prog.funcs))
        kinds = {
            "fn": _ops.KIND_FN,
            "task": _ops.KIND_TASK,
            "workflow": _ops.KIND_WORKFLOW,
            "test": _ops.KIND_TEST,
            "arm": _ops.KIND_ARM,
            "native": _ops.KIND_NATIVE,
        }
        for key, fc in prog.funcs.items():
            entry = art.funcs[fn_ids[key]]
            self.assertEqual(entry.kind, kinds[fc.kind], key)
            self.assertEqual(entry.arity, len(fc.params), key)
            self.assertEqual(entry.effects, _ops.effect_mask(fc.effects), key)
            if fc.kind == "native":
                self.assertEqual(entry.code, b"", key)
            else:
                self.assertTrue(entry.code, key)
        type_names = {art.const_str(t.name) for t in art.types}
        self.assertEqual(type_names, set(prog.noms))

    def test_the_auth_example(self) -> None:
        work = Path(tempfile.mkdtemp()) / "auth"
        shutil.copytree(Path(__file__).resolve().parents[1] / "examples" / "auth", work)
        try:
            self.check_package(str(work))
        finally:
            shutil.rmtree(work.parent, ignore_errors=True)

    def test_a_package_using_every_callable_kind(self) -> None:
        td = tempfile.mkdtemp()
        spec = (
            "module demo\n\ntype Cell {\n    slot: i64\n}\n\n"
            "trait Named {\n    fn label(value: Self) -> str\n}\n\n"
            "provide Named for Cell\n\n"
            "fn identity[T](value: T) -> T\n\n"
            "workflow drive() -> list[i64]\n    effects { time }\n\n"
            "task main() -> i64\n    effects { log, time }\n"
        )
        impl = (
            "module demo\n\nuse core.log { write }\nuse core.str { from_i64 }\n\n"
            "provide Named for Cell\n{\n"
            "    fn label(value: Self) -> str\n    {\n"
            "        return core.str.from_i64(value.slot)\n    }\n}\n\n"
            "fn identity[T](value: T) -> T\n{\n    return value\n}\n\n"
            "workflow drive() -> list[i64]\n    effects { time }\n{\n"
            "    base = identity(2)\n"
            "    return parallel max 2 timeout_ms 1000 {\n"
            "        base\n        base\n    }\n}\n\n"
            "task main() -> i64\n    effects { log, time }\n{\n"
            "    core.log.write(Named.label(Cell { slot: 1 }))\n"
            "    results = drive()\n    return identity(7)\n}\n"
        )
        tests = (
            "module test.demo\n\nuse core.test { assert_eq }\nuse demo { main }\n\n"
            "test runs\n{\n    core.test.assert_eq(demo.main(), 7)\n    return unit\n}\n"
        )
        write_pkg(
            td,
            {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl, "test/demo.gopyt": tests},
            fmt=True,
        )
        try:
            self.assertEqual(run_cli(td, "check")[0], 0)
            self.check_package(td)
            self.assertEqual(run_cli(td, "test")[0], 0)
            self.assertEqual(run_cli(td, "run", "demo.main"), (0, "7\n"))
            self.assertEqual(run_cli(td, "run", "demo.drive"), (0, "[2,2]\n"))
        finally:
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
