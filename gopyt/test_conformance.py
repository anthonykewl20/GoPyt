"""docs/conformance.md C001-C041, executable (P1 in docs/validation.md).

Do not "fix" a snippet to a dialect: if a case fails, the compiler is wrong.
"""

from __future__ import annotations

import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from gopyt import ops
from gopyt.gobyte import decode, encode
from gopyt.manifest import TOOLCHAIN
from gopyt.testing import check, diag, diag_code, run_cli, write_pkg

MATH_SPEC = """module math

fn add(left: i64, right: i64) -> i64
    requires true
    ensures result == left + right
"""

MATH_IMPL = """module math

fn add(left: i64, right: i64) -> i64
    requires true
    ensures result == left + right
{
    return left + right
}
"""


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td)

    def pkg(self, files: dict[str, str], **kw) -> str:
        write_pkg(self.td, files, **kw)
        return self.td

    def code(self, files: dict[str, str], **kw) -> int:
        write_pkg(self.td, files, **kw)
        return diag_code(self.td)


class MustAccept(Case):
    def test_c001_pure_i64(self) -> None:
        self.pkg({"spec/math.gopyt": MATH_SPEC, "impl/math.gopyt": MATH_IMPL})
        self.assertEqual(run_cli(self.td, "check")[0], 0)

    def test_c014_param_comma(self) -> None:
        self.assertEqual(
            self.code(
                {
                    "spec/math.gopyt": "module math\n\nfn pair(left: i64, right: i64) -> i64\n",
                    "impl/math.gopyt": (
                        "module math\n\nfn pair(left: i64, right: i64) -> i64\n{\n"
                        "    return left + right\n}\n"
                    ),
                }
            ),
            0,
        )

    def test_c018_return_unit(self) -> None:
        self.assertEqual(
            self.code(
                {
                    "spec/math.gopyt": "module math\n\nfn nothing() -> unit\n",
                    "impl/math.gopyt": "module math\n\nfn nothing() -> unit\n{\n    return unit\n}\n",
                }
            ),
            0,
        )


class MustReject(Case):
    def test_c002_missing_use(self) -> None:
        err = diag(
            self.pkg(
                {
                    # docs/conformance.md C002 verbatim: the header is `-> i64`.
                    # E013 must be reported before the return type is judged.
                    "spec/orders.gopyt": "module orders\n\ntask ping() -> i64\n    effects { log }\n",
                    "impl/orders.gopyt": (
                        "module orders\n\ntask ping() -> i64\n    effects { log }\n{\n"
                        '    return core.log.write("x")\n}\n'
                    ),
                }
            )
        )
        self.assertEqual(err.diag.code, 13)
        self.assertEqual(err.diag.repair, "use core.log { write }\n")

    def test_c003_inexhaustive_match(self) -> None:
        spec = (
            "module orders\n\nuse core.status { NotFound }\n\ntype User {\n    id: i64\n}\n\n"
            "fn name_of(value: User | NotFound) -> i64\n"
        )
        impl = (
            "module orders\n\nuse core.status { NotFound }\n\n"
            "fn name_of(value: User | NotFound) -> i64\n{\n"
            "    return match value {\n        User { id } -> id\n    }\n}\n"
        )
        self.assertEqual(
            self.code({"spec/orders.gopyt": spec, "impl/orders.gopyt": impl}), 70
        )

    def test_c004_fn_cannot_call_task(self) -> None:
        self.assertEqual(
            self.code(
                {
                    "spec/orders.gopyt": "module orders\n\nfn ping() -> unit\n",
                    "impl/orders.gopyt": (
                        "module orders\n\nuse core.log { write }\n\nfn ping() -> unit\n{\n"
                        '    return core.log.write("x")\n}\n'
                    ),
                }
            ),
            53,
        )

    def test_c005_int_is_not_a_type(self) -> None:
        self.assertEqual(
            self.code({"spec/math.gopyt": "module math\n\nfn add(value: int) -> i64\n"}, lock=False),
            20,
        )

    def test_c006_userid_illegal(self) -> None:
        self.assertEqual(
            self.code({"spec/math.gopyt": "module math\n\ntype UserID {\n}\n"}, lock=False), 16
        )

    def test_c007_leading_zero(self) -> None:
        self.assertEqual(
            self.code(
                {
                    "spec/math.gopyt": "module math\n\nfn one() -> i64\n",
                    "impl/math.gopyt": "module math\n\nfn one() -> i64\n{\n    return 01\n}\n",
                },
                lock=False,
            ),
            10,
        )

    def test_c008_if_is_not_an_expression(self) -> None:
        impl = (
            "module math\n\nfn one() -> i64\n{\n"
            "    number = if true {\n        return 1\n    } else {\n        return 0\n    }\n"
            "    return number\n}\n"
        )
        self.assertEqual(
            self.code(
                {"spec/math.gopyt": "module math\n\nfn one() -> i64\n", "impl/math.gopyt": impl},
                lock=False,
            ),
            11,
        )

    def test_c009_open_fails_check(self) -> None:
        spec = "module math\n\nfn add(left: i64, right: i64) -> i64\n    requires open needs_gateway\n"
        impl = (
            "module math\n\nfn add(left: i64, right: i64) -> i64\n"
            "    requires open needs_gateway\n{\n    return left + right\n}\n"
        )
        self.assertEqual(self.code({"spec/math.gopyt": spec, "impl/math.gopyt": impl}), 65)

    def test_c010_parallel_requires_bounds(self) -> None:
        impl = (
            "module math\n\ntask pair() -> list[i64]\n    effects { time }\n{\n"
            "    return parallel {\n        1\n        2\n    }\n}\n"
        )
        self.assertEqual(
            self.code(
                {
                    "spec/math.gopyt": "module math\n\ntask pair() -> list[i64]\n    effects { time }\n",
                    "impl/math.gopyt": impl,
                },
                lock=False,
            ),
            54,
        )

    def test_c011_json_requires_provide(self) -> None:
        spec = (
            "module billing\n\nuse core.status { ConvertError }\n\n"
            "type Payment {\n    amount: i64\n}\n\n"
            "fn encode(payment: Payment) -> str | ConvertError\n"
        )
        impl = (
            "module billing\n\nuse core.status { ConvertError }\nuse data.json { encode }\n\n"
            "fn encode(payment: Payment) -> str | ConvertError\n{\n"
            "    return data.json.encode(payment)\n}\n"
        )
        self.assertEqual(self.code({"spec/billing.gopyt": spec, "impl/billing.gopyt": impl}), 97)

    def test_c012_serve_arity(self) -> None:
        root = self.pkg(HTTP_FILES)
        self.assertEqual(run_cli(root, "check")[0], 0)
        code, out = run_cli(root, "run", "billing.api.post_charge")
        self.assertEqual(code, 1)
        self.assertIn("GOPYT_E022", out)

    def test_c013_param_comma_required(self) -> None:
        self.assertEqual(
            self.code(
                {"spec/math.gopyt": "module math\n\nfn pair(left: i64 right: i64) -> i64\n"},
                lock=False,
            ),
            11,
        )

    def test_c015_effect_comma_required(self) -> None:
        self.assertEqual(
            self.code(
                {
                    "spec/orders.gopyt": (
                        "module orders\n\ntask ping() -> unit\n"
                        "    effects { network database.write }\n"
                    )
                },
                lock=False,
            ),
            11,
        )

    def test_c016_no_trailing_comma(self) -> None:
        self.assertEqual(
            self.code(
                {
                    "spec/orders.gopyt": (
                        "module orders\n\ntask ping() -> unit\n"
                        "    effects { network, database.write, }\n"
                    )
                },
                lock=False,
            ),
            11,
        )

    def test_c017_missing_return_path(self) -> None:
        self.assertEqual(
            self.code(
                {
                    "spec/math.gopyt": "module math\n\nfn nothing() -> unit\n",
                    "impl/math.gopyt": "module math\n\nfn nothing() -> unit\n{\n}\n",
                },
                lock=False,
            ),
            73,
        )

    def test_c021_map_key_is_closed(self) -> None:
        self.assertEqual(
            self.code(
                {"spec/math.gopyt": "module math\n\nfn lookup(table: map[bytes, str]) -> i64\n"},
                lock=False,
            ),
            29,
        )

    def test_c023_parallel_max_zero(self) -> None:
        impl = (
            "module math\n\ntask one() -> list[i64]\n    effects { time }\n{\n"
            "    return parallel max 0 timeout_ms 1000 {\n        1\n    }\n}\n"
        )
        self.assertEqual(
            self.code(
                {
                    "spec/math.gopyt": "module math\n\ntask one() -> list[i64]\n    effects { time }\n",
                    "impl/math.gopyt": impl,
                },
                lock=False,
            ),
            54,
        )

    def test_c025_if_in_value_position(self) -> None:
        impl = (
            "module math\n\nfn one() -> i64\n{\n"
            "    return if true {\n        return 1\n    }\n}\n"
        )
        self.assertEqual(
            self.code(
                {"spec/math.gopyt": "module math\n\nfn one() -> i64\n", "impl/math.gopyt": impl},
                lock=False,
            ),
            71,
        )

    def test_c028_unknown_effect(self) -> None:
        self.assertEqual(
            self.code(
                {"spec/orders.gopyt": "module orders\n\ntask ping() -> unit\n    effects { http }\n"},
                lock=False,
            ),
            52,
        )

    def test_c029_non_test_open(self) -> None:
        spec = "module math\n\nfn add(left: i64, right: i64) -> i64\n    requires open needs_thought\n"
        impl = (
            "module math\n\nfn add(left: i64, right: i64) -> i64\n"
            "    requires open needs_thought\n{\n    return left + right\n}\n"
        )
        self.assertEqual(self.code({"spec/math.gopyt": spec, "impl/math.gopyt": impl}), 65)

    def test_c034_app_ffi(self) -> None:
        self.assertEqual(
            self.code(
                {"spec/orders.gopyt": "module orders\n\ntask ping() -> unit\n    effects { ffi }\n"},
                lock=False,
            ),
            69,
        )

    def test_c035_request_without_egress(self) -> None:
        spec = (
            "module billing\n\nuse core.status { HttpError }\nuse net.http { HttpResponse }\n\n"
            "task fetch() -> HttpResponse | HttpError\n    effects { network }\n"
        )
        impl = (
            "module billing\n\nuse core.status { HttpError }\n"
            "use core.bytes { from_str }\n"
            "use net.http { HttpMethod, HttpRequest, HttpResponse, request }\n\n"
            "task fetch() -> HttpResponse | HttpError\n    effects { network }\n{\n"
            "    req = HttpRequest {\n        method: HttpMethod.Get\n"
            '        url: "https://api.stripe.com/v1"\n'
            "        body: core.bytes.from_str(\"\")\n    }\n"
            "    return net.http.request(req)\n}\n"
        )
        self.assertEqual(self.code({"spec/billing.gopyt": spec, "impl/billing.gopyt": impl}), 111)

    def test_c036_secret_in_log(self) -> None:
        spec = "module billing\n\ntask leak() -> unit\n    effects { log, secret }\n"
        impl = (
            "module billing\n\nuse core.log { write }\nuse core.secret { Secret, get }\n"
            "use core.status { NotFound }\n\n"
            "task leak() -> unit\n    effects { log, secret }\n{\n"
            '    value = core.secret.get("api_token")\n'
            "    return match value {\n"
            "        Secret -> core.log.write(value)\n"
            "        NotFound -> unit\n"
            "    }\n}\n"
        )
        self.assertEqual(
            self.code({"spec/billing.gopyt": spec, "impl/billing.gopyt": impl}, lock=False), 112
        )

    def test_c038_eval(self) -> None:
        impl = "module math\n\nfn one() -> i64\n{\n    return eval(1)\n}\n"
        self.assertEqual(
            self.code(
                {"spec/math.gopyt": "module math\n\nfn one() -> i64\n", "impl/math.gopyt": impl},
                lock=False,
            ),
            110,
        )

    def test_c039_evolve_without_model(self) -> None:
        spec = (
            "module billing\n\nagent BillingAgent\n    effects { log }\n    tasks { collect }\n"
            "    evolve {\n        max 4\n        timeout_ms 30000\n        reservoir 32\n    }\n\n"
            "task collect() -> unit\n    effects { log }\n"
        )
        self.assertEqual(self.code({"spec/billing.gopyt": spec}, lock=False), 114)

    def test_c041_evolve_bounds(self) -> None:
        spec = (
            "module billing\n\nagent BillingAgent\n    effects { model }\n    tasks { collect }\n"
            "    evolve {\n        max 0\n        timeout_ms 1\n        reservoir 1\n    }\n\n"
            "task collect() -> unit\n    effects { model }\n"
        )
        self.assertEqual(self.code({"spec/billing.gopyt": spec}, lock=False), 117)


HTTP_FILES = {
    "spec/billing/api.gopyt": (
        "module billing.api\n\nuse core.convert { Json }\n"
        "use core.status { ListenError }\n\n"
        "type Payment {\n    amount: i64\n}\n\n"
        "type Receipt {\n    amount: i64\n}\n\n"
        "provide Json for Payment\n\n"
        "provide Json for Receipt\n\n"
        "http {\n    post \"/charge\" post_charge\n}\n\n"
        "task post_charge(payment: Payment) -> Receipt\n    effects { log }\n\n"
        "task serve() -> unit | ListenError\n    effects { network, log }\n"
    ),
    "impl/billing/api.gopyt": (
        "module billing.api\n\nuse core.log { write }\nuse core.status { ListenError }\n"
        "use net.http { serve }\n\n"
        "task post_charge(payment: Payment) -> Receipt\n    effects { log }\n{\n"
        '    core.log.write("charge")\n'
        "    return Receipt { amount: payment.amount }\n}\n\n"
        "task serve() -> unit | ListenError\n    effects { network, log }\n{\n"
        "    return net.http.serve()\n}\n"
    ),
}


class Assertions(Case):
    def test_c019_list_empty_type(self) -> None:
        spec = (
            "module billing\n\ntype Payment {\n    amount: i64\n}\n\n"
            "fn none_yet() -> list[Payment]\n"
        )
        impl = (
            "module billing\n\nuse core.list { empty }\n\n"
            "fn none_yet() -> list[Payment]\n{\n    return core.list.empty[Payment]()\n}\n"
        )
        prog = check(self.pkg({"spec/billing.gopyt": spec, "impl/billing.gopyt": impl}))
        self.assertEqual(prog.funcs["billing.none_yet"].ret.key, "list[billing.Payment]")

    def test_c020_map_empty_type(self) -> None:
        spec = (
            "module billing\n\ntype Payment {\n    amount: i64\n}\n\n"
            "fn table() -> map[str, Payment]\n"
        )
        impl = (
            "module billing\n\nuse core.map { empty }\n\n"
            "fn table() -> map[str, Payment]\n{\n    return core.map.empty[str, Payment]()\n}\n"
        )
        prog = check(self.pkg({"spec/billing.gopyt": spec, "impl/billing.gopyt": impl}))
        self.assertEqual(prog.funcs["billing.table"].ret.key, "map[str, billing.Payment]")

    def test_c022_parallel_runs_in_source_order(self) -> None:
        spec = "module math\n\ntask three() -> list[i64]\n    effects { time }\n"
        impl = (
            "module math\n\ntask three() -> list[i64]\n    effects { time }\n{\n"
            "    return parallel max 2 timeout_ms 1000 {\n        1\n        2\n        3\n    }\n}\n"
        )
        root = self.pkg({"spec/math.gopyt": spec, "impl/math.gopyt": impl})
        self.assertEqual(run_cli(root, "check")[0], 0)
        code, out = run_cli(root, "run", "math.three")
        self.assertEqual(code, 0)
        self.assertEqual(out, "[1,2,3]\n")
        data = Path(root, "build/out.gobyte").read_bytes()
        art = decode(data)
        arm_count = sum(1 for f in art.funcs if f.kind == ops.KIND_ARM)
        self.assertEqual(arm_count, 3)

    def test_c024_division_by_zero_traps(self) -> None:
        spec = "module math\n\ntask boom() -> i64\n    effects { log }\n"
        impl = (
            "module math\n\nuse core.log { write }\n\ntask boom() -> i64\n    effects { log }\n{\n"
            '    core.log.write("start")\n'
            "    zero = 0\n    return 1 / zero\n}\n"
        )
        root = self.pkg({"spec/math.gopyt": spec, "impl/math.gopyt": impl})
        code, out = run_cli(root, "run", "math.boom")
        self.assertEqual(code, 2)
        self.assertIn("GOPYT_E101 trap", out)
        self.assertIn("trap: 4", out)

    def test_c026_format_is_idempotent(self) -> None:
        root = self.pkg({"spec/math.gopyt": MATH_SPEC, "impl/math.gopyt": MATH_IMPL})
        run_cli(root, "fmt")
        once = Path(root, "impl/math.gopyt").read_bytes()
        run_cli(root, "fmt")
        twice = Path(root, "impl/math.gopyt").read_bytes()
        self.assertEqual(once, twice)

    def test_c027_jump_into_operand(self) -> None:
        spec = "module math\n\nfn pick(flag: bool) -> i64\n"
        impl = (
            "module math\n\nfn pick(flag: bool) -> i64\n{\n"
            "    if flag {\n        return 1\n    }\n    return 0\n}\n"
        )
        root = self.pkg({"spec/math.gopyt": spec, "impl/math.gopyt": impl})
        self.assertEqual(run_cli(root, "check")[0], 0)
        data = bytearray(Path(root, "build/out.gobyte").read_bytes())
        art = decode(bytes(data))
        target = next(f for f in art.funcs if art.const_str(f.name) == "math.pick")
        code = bytearray(target.code)
        pos = code.index(ops.JUMP_IF_FALSE)
        struct.pack_into("<i", code, pos + 1, struct.unpack_from("<i", code, pos + 1)[0] + 1)
        target.code = bytes(code)
        with self.assertRaises(Exception) as cm:
            decode(encode(art))
        self.assertEqual(cm.exception.diag.code, 100)

    def test_c030_stale_lock(self) -> None:
        root = self.pkg({"spec/math.gopyt": MATH_SPEC, "impl/math.gopyt": MATH_IMPL})
        Path(root, "gopyt.lock").write_text(f'toolchain = "{TOOLCHAIN}"\n', encoding="utf-8")
        err = diag(root)
        self.assertEqual(err.diag.code, 41)
        self.assertEqual(err.diag.repair.count("[[pkg]]"), 1)
        Path(root, "gopyt.lock").write_text(err.diag.repair, encoding="utf-8")
        self.assertEqual(diag_code(root), 0)

    def test_c031_test_effects_are_inferred(self) -> None:
        spec = "module orders\n\nfn ok() -> i64\n"
        impl = "module orders\n\nfn ok() -> i64\n{\n    return 1\n}\n"
        test = (
            "module test.orders\n\nuse store.db { put }\n\n"
            "test writes_db\n{\n"
            '    store.db.put("c031", "value")\n'
            "    return unit\n}\n"
        )
        root = self.pkg(
            {"spec/orders.gopyt": spec, "impl/orders.gopyt": impl, "test/orders.gopyt": test}
        )
        prog = check(root)
        self.assertEqual(
            prog.funcs["test.orders.writes_db"].effects, frozenset({"database.write"})
        )
        art = decode(Path(root, "build/out.gobyte").read_bytes()) if False else None
        self.assertEqual(run_cli(root, "test")[0], 0)

    def test_c032_effectful_test_uses_the_runtime(self) -> None:
        spec = "module orders\n\nfn ok() -> i64\n"
        impl = "module orders\n\nfn ok() -> i64\n{\n    return 1\n}\n"
        test = (
            "module test.orders\n\nuse core.test { assert_eq }\nuse store.db { get, put }\n\n"
            "test round_trips\n{\n"
            '    store.db.put("c032", "value")\n'
            '    core.test.assert_eq(store.db.get("c032"), "value")\n'
            "    return unit\n}\n"
        )
        root = self.pkg(
            {"spec/orders.gopyt": spec, "impl/orders.gopyt": impl, "test/orders.gopyt": test}
        )
        self.assertEqual(run_cli(root, "test")[0], 0)

    def test_c033_obligation_discharged(self) -> None:
        spec = "module math\n\nfn charge_zero(value: i64) -> i64\n    requires open needs_test_charge_zero\n"
        impl = (
            "module math\n\nfn charge_zero(value: i64) -> i64\n"
            "    requires open needs_test_charge_zero\n{\n    return value\n}\n"
        )
        test = (
            "module test.math\n\nuse core.test { assert_eq }\nuse math { charge_zero }\n\n"
            "test charge_zero\n{\n"
            "    core.test.assert_eq(math.charge_zero(0), 0)\n    return unit\n}\n"
        )
        root = self.pkg(
            {"spec/math.gopyt": spec, "impl/math.gopyt": impl, "test/math.gopyt": test}
        )
        self.assertEqual(diag_code(root), 0)
        self.assertEqual(run_cli(root, "test")[0], 0)

    def test_c037_json_extra_key(self) -> None:
        spec = (
            "module billing\n\nuse core.convert { Json }\nuse core.status { ConvertError }\n\n"
            "type Payment {\n    amount: i64\n}\n\n"
            "provide Json for Payment\n\n"
            "task load() -> Payment | ConvertError\n    effects { log }\n"
        )
        impl = (
            "module billing\n\nuse core.log { write }\nuse core.status { ConvertError }\n"
            "use data.json { decode }\n\n"
            "task load() -> Payment | ConvertError\n    effects { log }\n{\n"
            '    core.log.write("decode")\n'
            '    return data.json.decode[Payment]("{\\"amount\\": 1, \\"extra\\": 2}")\n}\n'
        )
        root = self.pkg({"spec/billing.gopyt": spec, "impl/billing.gopyt": impl})
        code, out = run_cli(root, "run", "billing.load")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith('{"core.status.ConvertError"'), out)

    def test_c040_propose_needs_evolve(self) -> None:
        spec = (
            "module billing\n\nuse core.evolve { Applied, EvolveError, NoChange }\n\n"
            "task tick() -> Applied | NoChange | EvolveError\n"
            "    effects { model, time, log, observe, filesystem.read, filesystem.write }\n"
        )
        impl = (
            "module billing\n\nuse core.evolve { Applied, EvolveError, NoChange, propose }\n\n"
            "task tick() -> Applied | NoChange | EvolveError\n"
            "    effects { model, time, log, observe, filesystem.read, filesystem.write }\n{\n"
            "    return core.evolve.propose()\n}\n"
        )
        self.assertEqual(
            self.code({"spec/billing.gopyt": spec, "impl/billing.gopyt": impl}, lock=False), 115
        )


if __name__ == "__main__":
    unittest.main()
