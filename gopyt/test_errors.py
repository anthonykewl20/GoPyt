"""One package per diagnostic in docs/diagnostics.md.

Each case is the smallest legal-looking program that must be rejected, so the
codes an agent keys on stay pinned to real inputs.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from gopyt.diag import CompileError
from gopyt.testing import diag_code, write_pkg

STATUS = "use core.status { NotFound }\n"


class Errors(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def code(self, spec: str, impl: str | None = None, **extra: str) -> int:
        files = {"spec/demo.gopyt": spec}
        if impl is not None:
            files["impl/demo.gopyt"] = impl
        files.update(extra)
        write_pkg(self.td, files, lock=False)
        return diag_code(self.td)

    # -- layout and imports ----------------------------------------------

    def test_e004_unformatted(self) -> None:
        self.assertEqual(
            self.code(
                "module demo\n\n\n\nfn one() -> i64\n",
                "module demo\n\nfn one() -> i64\n{\n    return 1\n}\n",
            ),
            4,
        )

    def test_e014_unused_use(self) -> None:
        self.assertEqual(
            self.code(
                "module demo\n\n" + STATUS + "\nfn one() -> i64\n",
                "module demo\n\nfn one() -> i64\n{\n    return 1\n}\n",
            ),
            14,
        )

    def test_e018_impl_without_a_spec_twin(self) -> None:
        write_pkg(
            self.td,
            {"impl/orphan.gopyt": "module orphan\n\nfn one() -> i64\n{\n    return 1\n}\n"},
            lock=False,
        )
        self.assertEqual(diag_code(self.td), 18)

    def test_e019_impl_provide_without_a_spec_twin(self) -> None:
        spec = "module demo\n\ntrait Named {\n    fn name(value: Self) -> str\n}\n\ntype User {\n    tag: str\n}\n"
        impl = (
            "module demo\n\nprovide Named for User\n{\n"
            "    fn name(value: Self) -> str\n    {\n        return value.tag\n    }\n}\n"
        )
        self.assertEqual(self.code(spec, impl), 19)

    def test_e096_unknown_stdlib_module(self) -> None:
        self.assertEqual(
            self.code(
                "module demo\n\nfn one() -> i64\n",
                "module demo\n\nuse core.nope { thing }\n\nfn one() -> i64\n{\n"
                "    return core.nope.thing()\n}\n",
            ),
            96,
        )

    # -- declarations ----------------------------------------------------

    def test_e004_unused_egress_block(self) -> None:
        """security.md: omit `egress` when nothing goes out."""
        spec = (
            "module demo\n\negress { \"https://api.example\" }\n\n"
            "task ping() -> unit\n    effects { log }\n"
        )
        impl = (
            "module demo\n\nuse core.log { write }\n\n"
            "task ping() -> unit\n    effects { log }\n{\n"
            '    return core.log.write("x")\n}\n'
        )
        self.assertEqual(self.code(spec, impl), 4)

    def test_e004_duplicate_egress_origin(self) -> None:
        spec = (
            "module demo\n\n"
            'egress { "https://api.example", "https://api.example:443" }\n\n'
            "task ping() -> unit\n    effects { network }\n"
        )
        self.assertEqual(self.code(spec), 4)

    def test_e096_unknown_name_in_an_allowlist(self) -> None:
        self.assertEqual(
            self.code(
                "module demo\n\nuse core.log { nope }\n\nfn one() -> i64\n",
            ),
            96,
        )

    def test_e096_unknown_app_module(self) -> None:
        self.assertEqual(
            self.code("module demo\n\nuse other.thing { name }\n\nfn one() -> i64\n"), 96
        )

    def test_e028_builtin_shadow(self) -> None:
        self.assertEqual(self.code("module demo\n\ntype Str {\n    value: i64\n}\n"), 28)

    def test_e031_body_in_spec(self) -> None:
        self.assertEqual(
            self.code("module demo\n\nfn one() -> i64\n{\n    return 1\n}\n"), 31
        )

    def test_e033_missing_impl(self) -> None:
        self.assertEqual(self.code("module demo\n\nfn one() -> i64\n"), 33)

    def test_e034_unresolved_in_spec(self) -> None:
        self.assertEqual(
            self.code(
                "module demo\n\nfn one() -> i64\n{\n    unresolved one\n}\n",
            ),
            31,
        )

    def test_e034_unresolved_in_a_spec_role_body(self) -> None:
        """`unresolved` is illegal in spec/ even where a body is (E031 first)."""
        write_pkg(
            self.td,
            {
                "spec/demo.gopyt": "module demo\n\nfn one() -> i64\n",
                "impl/demo.gopyt": "module demo\n\nfn one() -> i64\n{\n    unresolved one\n}\n",
            },
            lock=False,
        )
        self.assertEqual(diag_code(self.td), 30)

    # -- types and values ------------------------------------------------

    def test_e023_missing_field(self) -> None:
        spec = "module demo\n\ntype Pair {\n    left: i64\n    right: i64\n}\n\nfn make() -> Pair\n"
        impl = (
            "module demo\n\nfn make() -> Pair\n{\n"
            "    return Pair { left: 1 }\n}\n"
        )
        self.assertEqual(self.code(spec, impl), 23)

    def test_e024_extra_field(self) -> None:
        spec = "module demo\n\ntype One {\n    left: i64\n}\n\nfn make() -> One\n"
        impl = (
            "module demo\n\nfn make() -> One\n{\n"
            "    return One {\n        left: 1\n        other: 2\n    }\n}\n"
        )
        self.assertEqual(self.code(spec, impl), 24)

    def test_e025_illegal_union_member(self) -> None:
        self.assertEqual(
            self.code("module demo\n\n" + STATUS + "\nfn one() -> list[i64] | NotFound\n"),
            25,
        )

    def test_e026_optional_used_as_a_value(self) -> None:
        spec = "module demo\n\nfn first(items: list[i64]) -> i64\n"
        impl = (
            "module demo\n\nuse core.list { get }\n\n"
            "fn first(items: list[i64]) -> i64\n{\n"
            "    return core.list.get(items, 0)\n}\n"
        )
        self.assertEqual(self.code(spec, impl), 26)

    def test_e029_bad_type_arguments(self) -> None:
        self.assertEqual(self.code("module demo\n\nfn one(items: list[i64, str]) -> i64\n"), 29)

    def test_e036_rebind(self) -> None:
        spec = "module demo\n\nfn one() -> i64\n"
        impl = (
            "module demo\n\nfn one() -> i64\n{\n"
            "    value = 1\n    value = 2\n    return value\n}\n"
        )
        self.assertEqual(self.code(spec, impl), 36)

    def test_e099_f64_equality(self) -> None:
        spec = "module demo\n\nfn same(left: f64, right: f64) -> bool\n"
        impl = (
            "module demo\n\nfn same(left: f64, right: f64) -> bool\n{\n"
            "    return left == right\n}\n"
        )
        self.assertEqual(self.code(spec, impl), 99)

    def test_e072_for_over_a_scalar(self) -> None:
        spec = "module demo\n\nfn one() -> i64\n"
        impl = (
            "module demo\n\nfn one() -> i64\n{\n"
            "    for step in 5 {\n        return 0\n    }\n    return 1\n}\n"
        )
        self.assertEqual(self.code(spec, impl), 72)

    # -- effects and contracts -------------------------------------------

    def test_e050_effect_missing(self) -> None:
        spec = "module demo\n\ntask ping() -> unit\n    effects { time }\n"
        impl = (
            "module demo\n\nuse core.log { write }\nuse core.time { sleep_ms }\n\n"
            "task ping() -> unit\n    effects { time }\n{\n"
            "    core.time.sleep_ms(0)\n"
            '    return core.log.write("x")\n}\n'
        )
        self.assertEqual(self.code(spec, impl), 50)

    def test_e051_effect_unused(self) -> None:
        spec = "module demo\n\ntask ping() -> unit\n    effects { log, time }\n"
        impl = (
            "module demo\n\nuse core.log { write }\n\n"
            "task ping() -> unit\n    effects { log, time }\n{\n"
            '    return core.log.write("x")\n}\n'
        )
        self.assertEqual(self.code(spec, impl), 51)

    def test_e055_parallel_arm_types_differ(self) -> None:
        spec = "module demo\n\ntask pair() -> list[i64]\n    effects { time }\n"
        impl = (
            "module demo\n\ntask pair() -> list[i64]\n    effects { time }\n{\n"
            "    return parallel max 2 timeout_ms 1000 {\n        1\n"
            '        "two"\n    }\n}\n'
        )
        self.assertEqual(self.code(spec, impl), 55)

    def test_e061_f64_in_a_contract(self) -> None:
        """S20: v0 contracts are bool/i64/str/enum/T? only."""
        spec = (
            "module demo\n\nfn size(value: f64) -> i64\n\n"
            "fn scaled(value: f64) -> i64\n    requires size(value) > 0\n"
        )
        impl = (
            "module demo\n\nfn size(value: f64) -> i64\n{\n    return 1\n}\n\n"
            "fn scaled(value: f64) -> i64\n    requires size(value) > 0\n{\n"
            "    return size(value)\n}\n"
        )
        self.assertEqual(self.code(spec, impl), 61)

    def test_e062_contract_calls_a_task(self) -> None:
        spec = (
            "module demo\n\ntask ready() -> bool\n    effects { log }\n\n"
            "fn gated(value: i64) -> i64\n    requires ready()\n"
        )
        impl = (
            "module demo\n\nuse core.log { write }\n\n"
            "task ready() -> bool\n    effects { log }\n{\n"
            '    core.log.write("ready")\n    return true\n}\n\n'
            "fn gated(value: i64) -> i64\n    requires ready()\n{\n    return value\n}\n"
        )
        self.assertEqual(self.code(spec, impl), 62)

    def test_e060_contract_is_not_bool(self) -> None:
        self.assertEqual(
            self.code("module demo\n\nfn one(value: i64) -> i64\n    requires value\n"), 60
        )

    # -- traits ----------------------------------------------------------

    TRAIT = "trait Named {\n    fn name(value: Self) -> str\n}\n"

    def test_e080_provide_orphan(self) -> None:
        spec = (
            "module demo\n\nuse core.convert { FromStr }\n\n"
            "provide FromStr for i64\n"
        )
        self.assertEqual(self.code(spec), 80)

    def test_e081_provide_twice(self) -> None:
        spec = (
            "module demo\n\n" + self.TRAIT + "\ntype User {\n    tag: str\n}\n\n"
            "provide Named for User\n"
        )
        impl = (
            "module demo\n\nprovide Named for User\n{\n"
            "    fn name(value: Self) -> str\n    {\n        return value.tag\n    }\n}\n\n"
            "provide Named for User\n{\n"
            "    fn name(value: Self) -> str\n    {\n        return value.tag\n    }\n}\n"
        )
        self.assertEqual(self.code(spec, impl), 81)

    def test_e082_provide_signature_mismatch(self) -> None:
        spec = (
            "module demo\n\n" + self.TRAIT + "\ntype User {\n    tag: str\n}\n\n"
            "provide Named for User\n"
        )
        impl = (
            "module demo\n\nprovide Named for User\n{\n"
            "    fn name(value: Self) -> i64\n    {\n        return 1\n    }\n}\n"
        )
        self.assertEqual(self.code(spec, impl), 82)

    def test_e083_provide_widens_effects(self) -> None:
        spec = (
            "module demo\n\ntrait Store {\n    task save(value: Self) -> unit\n"
            "        effects { log }\n}\n\ntype User {\n    tag: str\n}\n\n"
            "provide Store for User\n"
        )
        impl = (
            "module demo\n\nuse core.log { write }\n\nprovide Store for User\n{\n"
            "    task save(value: Self) -> unit\n        effects { log, time }\n    {\n"
            "        return core.log.write(value.tag)\n    }\n}\n"
        )
        self.assertEqual(self.code(spec, impl), 83)

    def test_e084_fn_calls_an_effectful_trait_member(self) -> None:
        spec = (
            "module demo\n\ntrait Store {\n    task save(value: Self) -> unit\n"
            "        effects { log }\n}\n\ntype User {\n    tag: str\n}\n\n"
            "provide Store for User\n\nfn describe(value: User) -> unit\n"
        )
        impl = (
            "module demo\n\nuse core.log { write }\n\nprovide Store for User\n{\n"
            "    task save(value: Self) -> unit\n        effects { log }\n    {\n"
            "        return core.log.write(value.tag)\n    }\n}\n\n"
            "fn describe(value: User) -> unit\n{\n    return Store.save(value)\n}\n"
        )
        self.assertEqual(self.code(spec, impl), 84)

    def test_e035_private_type_in_a_public_signature(self) -> None:
        spec = "module demo\n\nfn make() -> Helper\n"
        impl = (
            "module demo\n\ntype Helper {\n    tag: str\n}\n\n"
            "fn make() -> Helper\n{\n"
            '    return Helper { tag: "x" }\n}\n'
        )
        self.assertEqual(self.code(spec, impl), 35)

    def test_e038_comma_in_a_field_list(self) -> None:
        self.assertEqual(
            self.code("module demo\n\ntype Pair {\n    left: i64,\n    right: i64\n}\n"), 38
        )

    def test_e040_toolchain_mismatch(self) -> None:
        write_pkg(
            self.td,
            {
                "spec/demo.gopyt": "module demo\n\nfn one() -> i64\n",
                "impl/demo.gopyt": "module demo\n\nfn one() -> i64\n{\n    return 1\n}\n",
            },
            lock=False,
        )
        Path(self.td, "gopyt.lock").write_text('toolchain = "gopyt-9.9.9"\n', encoding="utf-8")
        self.assertEqual(diag_code(self.td), 40)

    def test_e003_crlf_source(self) -> None:
        write_pkg(self.td, {"spec/demo.gopyt": "module demo\n\nfn one() -> i64\n"}, lock=False)
        Path(self.td, "spec/demo.gopyt").write_bytes(b"module demo\r\n\r\nfn one() -> i64\r\n")
        self.assertEqual(diag_code(self.td), 3)

    def test_e003_invalid_utf8_source(self) -> None:
        write_pkg(self.td, {"spec/demo.gopyt": "module demo\n\nfn one() -> i64\n"}, lock=False)
        Path(self.td, "spec/demo.gopyt").write_bytes(b"module demo\n\nfn one() -> i64 \xff\n")
        self.assertEqual(diag_code(self.td), 3)

    def test_e002_tab(self) -> None:
        write_pkg(
            self.td,
            {
                "spec/demo.gopyt": "module demo\n\nfn one() -> i64\n",
                "impl/demo.gopyt": "module demo\n\nfn one() -> i64\n{\n\treturn 1\n}\n",
            },
            lock=False,
        )
        self.assertEqual(diag_code(self.td), 2)

    def test_e010_module_path_too_deep(self) -> None:
        """implementer.md 7: at most eight path segments."""
        deep = ".".join(f"seg{i}" for i in range(9))
        self.assertEqual(self.code("module " + deep + "\n\nfn one() -> i64\n"), 10)

    def test_e032_impl_drifts_from_spec(self) -> None:
        cases = {
            "return type": (
                "module demo\n\nfn one() -> i64\n",
                "module demo\n\nfn one() -> str\n{\n    return \"1\"\n}\n",
            ),
            "parameter name": (
                "module demo\n\nfn one(left: i64) -> i64\n",
                "module demo\n\nfn one(right: i64) -> i64\n{\n    return right\n}\n",
            ),
            "parameter type": (
                "module demo\n\nfn one(left: i64) -> i64\n",
                "module demo\n\nfn one(left: i32) -> i64\n{\n    return 1\n}\n",
            ),
            "kind": (
                "module demo\n\nfn one() -> i64\n",
                "module demo\n\ntask one() -> i64\n    effects { log }\n{\n    return 1\n}\n",
            ),
            "effects": (
                "module demo\n\ntask one() -> i64\n    effects { log }\n",
                "module demo\n\ntask one() -> i64\n    effects { time }\n{\n    return 1\n}\n",
            ),
            "contract count": (
                "module demo\n\nfn one(value: i64) -> i64\n    requires value > 0\n",
                "module demo\n\nfn one(value: i64) -> i64\n{\n    return value\n}\n",
            ),
        }
        for name, (spec, impl) in cases.items():
            with self.subTest(drift=name):
                shutil.rmtree(self.td, ignore_errors=True)
                self.td = tempfile.mkdtemp()
                self.assertEqual(self.code(spec, impl), 32)

    def test_e012_module_header_mismatch(self) -> None:
        write_pkg(self.td, {"spec/demo.gopyt": "module other\n\nfn one() -> i64\n"}, lock=False)
        from gopyt.testing import diag

        err = diag(self.td)
        self.assertEqual(err.diag.code, 12)
        self.assertEqual(err.diag.repair, "module demo\n")

    def test_e017_keyword_as_a_name(self) -> None:
        self.assertEqual(self.code("module demo\n\nfn while() -> i64\n"), 17)

    def test_e022_wrong_argument_count(self) -> None:
        spec = "module demo\n\nfn add(left: i64, right: i64) -> i64\n\nfn one() -> i64\n"
        impl = (
            "module demo\n\nfn add(left: i64, right: i64) -> i64\n{\n"
            "    return left + right\n}\n\n"
            "fn one() -> i64\n{\n    return add(1)\n}\n"
        )
        self.assertEqual(self.code(spec, impl), 22)

    def test_e074_unknown_local(self) -> None:
        spec = "module demo\n\nfn one() -> i64\n"
        impl = "module demo\n\nfn one() -> i64\n{\n    return missing\n}\n"
        self.assertEqual(self.code(spec, impl), 74)

    def test_e065_obligation_only_on_a_public_fn(self) -> None:
        spec = (
            "module demo\n\ntask charge() -> unit\n    effects { log }\n"
            "    requires open needs_test_charge\n"
        )
        impl = (
            "module demo\n\nuse core.log { write }\n\ntask charge() -> unit\n"
            "    effects { log }\n    requires open needs_test_charge\n{\n"
            '    return core.log.write("x")\n}\n'
        )
        tests = (
            "module test.demo\n\nuse core.test { assert_eq }\n\n"
            "test charge\n{\n    core.test.assert_eq(1, 1)\n    return unit\n}\n"
        )
        write_pkg(
            self.td,
            {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl, "test/demo.gopyt": tests},
            lock=False,
        )
        self.assertEqual(diag_code(self.td), 65)

    def test_e065_duplicate_obligation_test(self) -> None:
        spec = "module demo\n\nfn charge() -> i64\n    requires open needs_test_charge\n"
        impl = (
            "module demo\n\nfn charge() -> i64\n    requires open needs_test_charge\n{\n"
            "    return 1\n}\n"
        )
        tests = (
            "module test.demo\n\nuse core.test { assert_eq }\n\n"
            "test charge\n{\n    core.test.assert_eq(1, 1)\n    return unit\n}\n\n"
            "test charge\n{\n    core.test.assert_eq(2, 2)\n    return unit\n}\n"
        )
        write_pkg(
            self.td,
            {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl, "test/demo.gopyt": tests},
            lock=False,
        )
        # diagnostics.md E065: "missing or duplicate obligated test".
        self.assertEqual(diag_code(self.td), 65)

    def test_e036_duplicate_test_name(self) -> None:
        spec = "module demo\n\nfn ok() -> i64\n"
        impl = "module demo\n\nfn ok() -> i64\n{\n    return 1\n}\n"
        tests = (
            "module test.demo\n\nuse core.test { assert_eq }\n\n"
            "test same\n{\n    core.test.assert_eq(1, 1)\n    return unit\n}\n\n"
            "test same\n{\n    core.test.assert_eq(2, 2)\n    return unit\n}\n"
        )
        write_pkg(
            self.td,
            {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl, "test/demo.gopyt": tests},
            lock=False,
        )
        self.assertEqual(diag_code(self.td), 36)

    # -- agents and http --------------------------------------------------

    def test_e091_route_without_a_handler(self) -> None:
        spec = (
            "module demo\n\nuse core.status { ListenError }\n\n"
            'http {\n    get "/thing" missing\n}\n\n'
            "task serve() -> unit | ListenError\n    effects { network }\n"
        )
        self.assertEqual(self.code(spec), 91)

    def test_e091_overlapping_routes(self) -> None:
        """implementer.md 12: no precedence rule, so an overlap is an error."""
        spec = (
            "module demo\n\nuse core.status { ListenError }\n\n"
            'http {\n    get "/thing/{id}" get_thing\n    get "/thing/all" get_all\n}\n\n'
            "task get_thing(id: str) -> str\n    effects { log }\n\n"
            "task get_all() -> str\n    effects { log }\n\n"
            "task serve() -> unit | ListenError\n    effects { log, network }\n"
        )
        self.assertEqual(self.code(spec), 91)

    def test_distinct_routes_are_accepted(self) -> None:
        spec = (
            "module demo\n\nuse core.status { ListenError }\n\n"
            'http {\n    get "/thing/{id}" get_thing\n    get "/other" get_all\n}\n\n'
            "task get_thing(id: str) -> str\n    effects { log }\n\n"
            "task get_all() -> str\n    effects { log }\n\n"
            "task serve() -> unit | ListenError\n    effects { log, network }\n"
        )
        impl = (
            "module demo\n\nuse core.log { write }\nuse core.status { ListenError }\n"
            "use net.http { serve }\n\n"
            "task get_thing(id: str) -> str\n    effects { log }\n{\n"
            '    core.log.write("thing")\n    return id\n}\n\n'
            "task get_all() -> str\n    effects { log }\n{\n"
            '    core.log.write("all")\n    return "all"\n}\n\n'
            "task serve() -> unit | ListenError\n    effects { log, network }\n{\n"
            "    return net.http.serve()\n}\n"
        )
        write_pkg(self.td, {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl}, fmt=True)
        self.assertEqual(diag_code(self.td), 0)

    def test_e091_illegal_route_path(self) -> None:
        for path in ("thing", "/thing/", "/thing?q=1", "/a/../b", "/a//b", "/{id}/{id}"):
            with self.subTest(path=path):
                spec = (
                    "module demo\n\nuse core.status { ListenError }\n\n"
                    f'http {{\n    get "{path}" get_thing\n}}\n\n'
                    "task get_thing(id: str) -> str\n    effects { log }\n\n"
                    "task serve() -> unit | ListenError\n    effects { log, network }\n"
                )
                shutil.rmtree(self.td, ignore_errors=True)
                self.td = tempfile.mkdtemp()
                self.assertEqual(self.code(spec), 91)

    def test_e111_bad_egress_origins(self) -> None:
        for origin in (
            "api.stripe.com",
            "https://api.stripe.com/v1",
            "https://user@api.stripe.com",
            "https://*.stripe.com",
            "http://example.com",
            "https://api.stripe.com:notaport",
        ):
            with self.subTest(origin=origin):
                shutil.rmtree(self.td, ignore_errors=True)
                self.td = tempfile.mkdtemp()
                spec = (
                    "module demo\n\n"
                    f'egress {{ "{origin}" }}\n\n'
                    "task ping() -> unit\n    effects { network }\n"
                )
                self.assertEqual(self.code(spec), 111)

    def test_egress_normalizes_host_and_port(self) -> None:
        from gopyt.check import normalize_origin

        self.assertEqual(normalize_origin("https://API.Stripe.com"), "https://api.stripe.com:443")
        self.assertEqual(normalize_origin("https://api.stripe.com:443"), "https://api.stripe.com:443")
        self.assertEqual(normalize_origin("http://localhost:8080"), "http://localhost:8080")
        self.assertEqual(normalize_origin("http://127.0.0.1"), "http://127.0.0.1:80")
        self.assertIsNone(normalize_origin("https://api.stripe.com/"))

    def test_e091_empty_http_block(self) -> None:
        spec = (
            "module demo\n\nuse core.status { ListenError }\n\nhttp {\n}\n\n"
            "task serve() -> unit | ListenError\n    effects { network }\n"
        )
        self.assertEqual(self.code(spec), 91)

    def test_e091_two_http_blocks(self) -> None:
        spec = (
            "module demo\n\nuse core.status { ListenError }\n\n"
            'http {\n    get "/one" get_one\n}\n\n'
            'http {\n    get "/two" get_two\n}\n\n'
            "task get_one() -> str\n    effects { log }\n\n"
            "task get_two() -> str\n    effects { log }\n\n"
            "task serve() -> unit | ListenError\n    effects { log, network }\n"
        )
        self.assertEqual(self.code(spec), 91)

    def test_e091_handler_must_be_a_task(self) -> None:
        spec = (
            "module demo\n\nuse core.status { ListenError }\n\n"
            'http {\n    get "/one" get_one\n}\n\n'
            "workflow get_one() -> str\n    effects { log }\n\n"
            "task serve() -> unit | ListenError\n    effects { log, network }\n"
        )
        self.assertEqual(self.code(spec), 91)

    def test_e092_placeholder_is_not_a_parameter(self) -> None:
        spec = (
            "module demo\n\nuse core.status { ListenError }\n\n"
            'http {\n    get "/thing/{id}" get_thing\n}\n\n'
            "task get_thing() -> str\n    effects { log }\n\n"
            "task serve() -> unit | ListenError\n    effects { log, network }\n"
        )
        self.assertEqual(self.code(spec), 92)

    def test_e093_serve_missing(self) -> None:
        spec = (
            "module demo\n\n"
            'http {\n    get "/thing" get_thing\n}\n\n'
            "task get_thing() -> str\n    effects { log }\n"
        )
        self.assertEqual(self.code(spec), 93)

    def test_e094_agent_lists_an_unknown_task(self) -> None:
        spec = (
            "module demo\n\nagent Worker\n    effects { log }\n    tasks { missing }\n\n"
            "task ping() -> unit\n    effects { log }\n"
        )
        self.assertEqual(self.code(spec), 94)

    def test_e095_agent_effects_are_not_the_union(self) -> None:
        spec = (
            "module demo\n\nagent Worker\n    effects { log, time }\n    tasks { ping }\n\n"
            "task ping() -> unit\n    effects { log }\n"
        )
        self.assertEqual(self.code(spec), 95)


class Lexical(unittest.TestCase):
    """docs/implementer.md 6: the literal forms, and only those."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def body(self, expr: str, ret: str = "i64") -> int:
        shutil.rmtree(self.td, ignore_errors=True)
        self.td = tempfile.mkdtemp()
        files = {
            "spec/demo.gopyt": f"module demo\n\nfn one() -> {ret}\n",
            "impl/demo.gopyt": f"module demo\n\nfn one() -> {ret}\n{{\n    return {expr}\n}}\n",
        }
        try:
            write_pkg(self.td, files)  # a legal package also gets its lock
        except CompileError as e:
            return e.diag.code
        return diag_code(self.td)

    def test_float_needs_digits_on_both_sides(self) -> None:
        self.assertEqual(self.body("1.", "f64"), 10)
        self.assertIn(self.body(".5", "f64"), (10, 11))

    def test_a_well_formed_float_is_accepted(self) -> None:
        self.assertEqual(self.body("1.5", "f64"), 0)

    def test_unary_minus_on_f64_is_illegal(self) -> None:
        self.assertEqual(self.body("-1.5", "f64"), 21)

    def test_hex_and_underscored_integers_do_not_exist(self) -> None:
        self.assertEqual(self.body("0x10"), 10)
        self.assertEqual(self.body("1_000"), 10)
        self.assertEqual(self.body("1i64"), 10)

    def test_only_the_five_escapes_exist(self) -> None:
        self.assertEqual(self.body('"tab\\there"', "str"), 0)
        self.assertEqual(self.body('"\\x41"', "str"), 10)

    def test_integer_must_fit_in_i64(self) -> None:
        self.assertEqual(self.body("9223372036854775808"), 10)

    def test_character_literals_do_not_exist(self) -> None:
        self.assertIn(self.body("'a'", "str"), (10, 11))


class RejectedForms(unittest.TestCase):
    """docs/conformance.md "must not exist": the shapes agents import from
    other languages are parse errors, not silent alternatives."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def expr(self, expr: str) -> int:
        shutil.rmtree(self.td, ignore_errors=True)
        self.td = tempfile.mkdtemp()
        files = {
            "spec/demo.gopyt": "module demo\n\nfn one(value: i64) -> bool\n",
            "impl/demo.gopyt": (
                f"module demo\n\nfn one(value: i64) -> bool\n{{\n    return {expr}\n}}\n"
            ),
        }
        try:
            write_pkg(self.td, files, lock=False)
        except CompileError as e:
            return e.diag.code
        return diag_code(self.td)

    def test_c_style_boolean_operators(self) -> None:
        self.assertEqual(self.expr("true && false"), 11)
        self.assertEqual(self.expr("true || false"), 11)
        self.assertEqual(self.expr("!true"), 11)

    def test_identity_comparison(self) -> None:
        self.assertEqual(self.expr("1 === 1"), 11)

    def test_a_method_call(self) -> None:
        self.assertEqual(self.expr("value.name()"), 37)

    def test_a_ternary(self) -> None:
        self.assertEqual(self.expr("1 if true else 0"), 11)

    def test_a_fat_arrow(self) -> None:
        self.assertEqual(
            self.expr("match value {\n        i64 => true\n    }"), 11
        )

    def test_the_words_are_the_operators(self) -> None:
        write_pkg(
            self.td,
            {
                "spec/demo.gopyt": "module demo\n\nfn one(value: i64) -> bool\n",
                "impl/demo.gopyt": (
                    "module demo\n\nfn one(value: i64) -> bool\n{\n"
                    "    return not (value > 0 and value < 10) or value == 0\n}\n"
                ),
            },
        )
        self.assertEqual(diag_code(self.td), 0)


class SecretRules(unittest.TestCase):
    """docs/security.md: an opaque value cannot be compared or encoded."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def test_secret_equality_is_rejected(self) -> None:
        spec = "module demo\n\ntask same() -> bool\n    effects { secret }\n"
        impl = (
            "module demo\n\nuse core.secret { get }\n\n"
            "task same() -> bool\n    effects { secret }\n{\n"
            '    left = core.secret.get("api_token")\n'
            '    right = core.secret.get("api_token")\n'
            "    return left == right\n}\n"
        )
        write_pkg(self.td, {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl}, lock=False)
        self.assertEqual(diag_code(self.td), 99)

    def test_a_record_holding_a_secret_is_not_json(self) -> None:
        spec = (
            "module demo\n\nuse core.convert { Json }\nuse core.secret { Secret }\n"
            "use core.status { ConvertError }\n\n"
            "type Holder {\n    token: Secret\n}\n\nprovide Json for Holder\n\n"
            "fn encode(value: Holder) -> str | ConvertError\n"
        )
        impl = (
            "module demo\n\nuse core.status { ConvertError }\nuse data.json { encode }\n\n"
            "fn encode(value: Holder) -> str | ConvertError\n{\n"
            "    return data.json.encode(value)\n}\n"
        )
        write_pkg(self.td, {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl}, lock=False)
        self.assertEqual(diag_code(self.td), 112)

    def test_user_code_cannot_construct_a_secret(self) -> None:
        spec = "module demo\n\nuse core.secret { Secret }\n\nfn make() -> Secret\n"
        impl = (
            "module demo\n\nuse core.secret { Secret }\n\n"
            "fn make() -> Secret\n{\n    return Secret {}\n}\n"
        )
        write_pkg(self.td, {"spec/demo.gopyt": spec, "impl/demo.gopyt": impl}, lock=False)
        self.assertEqual(diag_code(self.td), 21)


if __name__ == "__main__":
    unittest.main()
