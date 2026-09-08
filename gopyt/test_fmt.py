"""docs/fmt.md: one canonical layout, and `check` reports any deviation as E004."""

from __future__ import annotations

import shutil
import tempfile
import unittest

from gopyt.fmt import fmt_module
from gopyt.parser import parse_module
from gopyt.testing import write_pkg


def canonical(src: str, role: str = "spec") -> str:
    return fmt_module(parse_module(src, f"{role}/demo.gopyt", role))


class Canonical(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)

    def test_uses_are_sorted_and_names_grouped(self) -> None:
        src = (
            "module demo\n\nuse core.str { len, concat }\n"
            "use core.status { NotFound, DbError }\n\n"
            "fn one(text: str) -> i64\n"
        )
        out = canonical(src)
        self.assertIn("use core.status { DbError, NotFound }\n", out)
        self.assertIn("use core.str { concat, len }\n", out)
        self.assertLess(out.index("core.status"), out.index("core.str"))

    def test_effects_use_the_catalog_order_not_alphabetical(self) -> None:
        src = "module demo\n\ntask ping() -> unit\n    effects { log, network, database.write }\n"
        self.assertIn("effects { network, database.write, log }", canonical(src))

    def test_agent_tasks_and_egress_are_sorted(self) -> None:
        src = (
            "module demo\n\negress { \"https://b.example\", \"https://a.example\" }\n\n"
            "agent Worker\n    effects { log }\n    tasks { zeta, alpha }\n\n"
            "task alpha() -> unit\n    effects { log }\n\n"
            "task zeta() -> unit\n    effects { log }\n"
        )
        out = canonical(src)
        self.assertIn('egress { "https://a.example", "https://b.example" }', out)
        self.assertIn("tasks { alpha, zeta }", out)

    def test_empty_parameter_list_has_no_interior_space(self) -> None:
        self.assertIn("fn ping() -> unit", canonical("module demo\n\nfn ping() -> unit\n"))

    def test_one_blank_line_between_items_and_a_single_final_newline(self) -> None:
        src = "module demo\n\n\n\nfn one() -> i64\n\n\n\nfn two() -> i64\n\n\n"
        out = canonical(src)
        self.assertNotIn("\n\n\n", out)
        self.assertTrue(out.endswith("}\n") or out.endswith("i64\n"))
        self.assertFalse(out.endswith("\n\n"))

    def test_body_brace_is_on_its_own_line_and_headers_keep_theirs(self) -> None:
        src = (
            "module demo\n\nfn pick(flag: bool) -> i64\n{\n"
            "    if flag {\n        return 1\n    } else {\n        return 0\n    }\n}\n"
        )
        out = canonical(src, "impl")
        self.assertIn("fn pick(flag: bool) -> i64\n{\n", out)
        self.assertIn("    if flag {\n", out)
        self.assertIn("    } else {\n", out)

    def test_unformatted_source_is_e004_with_the_full_file_as_repair(self) -> None:
        write_pkg(
            self.td,
            {
                "spec/demo.gopyt": "module demo\n\n\nfn one() -> i64\n",
                "impl/demo.gopyt": "module demo\n\nfn one() -> i64\n{\n    return 1\n}\n",
            },
            lock=False,
        )
        from gopyt.testing import diag

        err = diag(self.td)
        self.assertEqual(err.diag.code, 4)
        self.assertTrue(err.diag.repair.startswith("module demo\n"))
        self.assertTrue(err.diag.repair.endswith("\n"))

    def test_formatting_is_a_fixed_point_for_every_construct(self) -> None:
        src = (
            "module demo\n\nuse core.list { range }\nuse core.log { write }\n\n"
            "type Payment {\n    amount: i64\n    currency: str\n}\n\n"
            "enum Status {\n    Pending\n    Paid {\n        amount: i64\n    }\n}\n\n"
            "trait Named {\n    fn name(value: Self) -> str\n}\n\n"
            "provide Named for Payment\n{\n"
            "    fn name(value: Self) -> str\n    {\n        return value.currency\n    }\n}\n\n"
            "agent Worker\n    effects { time, log }\n    tasks { drive }\n"
            "    evolve {\n        max 4\n        timeout_ms 30000\n        reservoir 32\n    }\n\n"
            "task drive(status: Status) -> list[i64]\n    effects { time, log }\n"
            "    requires true\n{\n"
            "    total = match status {\n        Status.Pending -> 0\n"
            "        Status.Paid { amount } -> amount\n    }\n"
            "    for step in core.list.range(0, total) {\n"
            '        core.log.write("step")\n    }\n'
            "    return parallel max 2 timeout_ms 1000 {\n        total\n        0\n    }\n}\n"
        )
        once = canonical(src, "impl")
        twice = canonical(once, "impl")
        self.assertEqual(once, twice)
        self.assertEqual(once, src)


def shape(node: object) -> object:
    """The parse tree without line numbers, for comparing before and after.

    `use` declarations are compared as a set: sorting them and their allowlists
    is canonicalization the formatter is required to do (docs/fmt.md), not a
    change of meaning.
    """
    import dataclasses

    from gopyt.ast_nodes import AgentDecl, EgressDecl, FnSig, Module, UseDecl

    if isinstance(node, Module):
        uses = tuple(sorted((u.path, tuple(sorted(set(u.names)))) for u in node.uses))
        return (
            "Module",
            node.path,
            node.role,
            uses,
            shape(node.items),
            shape(node.tests),
        )
    if isinstance(node, UseDecl):
        return ("UseDecl", node.path, tuple(sorted(set(node.names))))
    if isinstance(node, FnSig):
        # Effects are reordered into the S9 catalog order, and agent task names
        # and egress origins are sorted: all canonicalization, not meaning.
        return (
            "FnSig",
            node.kind,
            node.name,
            tuple(node.tparams),
            shape(node.params),
            shape(node.ret),
            tuple(sorted(node.effects)),
            shape(node.contracts),
        )
    if isinstance(node, AgentDecl):
        return (
            "AgentDecl",
            node.name,
            tuple(sorted(node.effects)),
            tuple(sorted(node.tasks)),
            node.evolve_max,
            node.evolve_timeout,
            node.evolve_reservoir,
        )
    if isinstance(node, EgressDecl):
        return ("EgressDecl", tuple(sorted(node.origins)))
    if dataclasses.is_dataclass(node):
        out = [type(node).__name__]
        for f in dataclasses.fields(node):
            if f.name in ("line", "comments", "trailing"):
                continue
            out.append((f.name, shape(getattr(node, f.name))))
        return tuple(out)
    if isinstance(node, (list, tuple)):
        return tuple(shape(x) for x in node)
    return node


class MeaningPreserved(unittest.TestCase):
    """A formatter that changes the parse tree is not a formatter."""

    CORPUS = [
        "module demo\n\nfn one(value: i64) -> bool\n{\n"
        "    return not (value > 0 and value < 10) or value == 0\n}\n",
        "module demo\n\nfn one() -> i64\n{\n    return (1 + 2) * 3\n}\n",
        "module demo\n\nfn one() -> i64\n{\n    return 1 - (2 - 3)\n}\n",
        "module demo\n\nfn one() -> i64\n{\n    return -(1 + 2)\n}\n",
        "module demo\n\nfn one() -> bool\n{\n    return not (true or false)\n}\n",
        "module demo\n\nfn one() -> i64\n{\n    return 2 * (3 + 4) % 5\n}\n",
        "module demo\n\nfn one(value: i64) -> bool\n{\n"
        "    return (value + 1) * 2 > 3 and not (value == 0)\n}\n",
        "module demo\n\nfn one(left: bool, right: bool) -> bool\n{\n"
        "    return left or right and not left\n}\n",
    ]

    def test_formatting_keeps_the_parse_tree(self) -> None:
        for src in self.CORPUS:
            with self.subTest(src=src):
                before = parse_module(src, "impl/demo.gopyt", "impl")
                once = fmt_module(before)
                after = parse_module(once, "impl/demo.gopyt", "impl")
                self.assertEqual(shape(before), shape(after))
                self.assertEqual(once, fmt_module(after))

    def test_needed_parentheses_survive(self) -> None:
        src = self.CORPUS[0]
        self.assertIn("not (value > 0 and value < 10)", fmt_module(
            parse_module(src, "impl/demo.gopyt", "impl")
        ))

    def test_redundant_parentheses_are_removed(self) -> None:
        src = "module demo\n\nfn one() -> i64\n{\n    return (1 + 2) + 3\n}\n"
        out = fmt_module(parse_module(src, "impl/demo.gopyt", "impl"))
        self.assertIn("return 1 + 2 + 3", out)

    def test_every_source_in_the_repository_keeps_its_parse_tree(self) -> None:
        """The real corpus: conformance fixtures, examples, and dep testdata."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        checked = 0
        for src_path in sorted(root.rglob("*.gopyt")):
            text = src_path.read_text(encoding="utf-8", errors="replace")
            rel = str(src_path.relative_to(root))
            role = "test" if "/test/" in rel else ("spec" if "/spec/" in rel else "impl")
            try:
                before = parse_module(text, rel, role)
                once = fmt_module(before)
                after = parse_module(once, rel, role)
            except Exception:
                continue  # a fixture that is meant to be rejected
            self.assertEqual(shape(before), shape(after), rel)
            self.assertEqual(once, fmt_module(after), rel)
            checked += 1
        self.assertGreater(checked, 30)

    def test_mutants_keep_their_parse_tree(self) -> None:
        import random

        from gopyt.test_fuzz import MutatedModules

        rng = random.Random(6060)
        mutator = MutatedModules()
        checked = 0
        for i in range(400):
            src = self.CORPUS[i % len(self.CORPUS)]
            for _ in range(rng.randrange(1, 3)):
                src = mutator.mutate(src, rng)
            try:
                before = parse_module(src, "impl/demo.gopyt", "impl")
                once = fmt_module(before)
                after = parse_module(once, "impl/demo.gopyt", "impl")
            except Exception:
                continue
            self.assertEqual(shape(before), shape(after), f"case {i}: {src!r}")
            checked += 1
        self.assertGreater(checked, 20)


if __name__ == "__main__":
    unittest.main()
