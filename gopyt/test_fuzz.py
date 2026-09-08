"""Frontend robustness: the compiler answers, it never crashes.

Every input below is garbage or near-garbage. The lexer, parser, and checker may
raise `CompileError` (a `GOPYT_E*` an agent can act on) or return normally.
Anything else — an uncaught exception, a hang — is a compiler bug, because
docs/spec.md S27 says an implementation that cannot proceed reports E001 and
stops. Seeded, so a failure is reproducible.
"""

from __future__ import annotations

import random
import shutil
import string
import tempfile
import unittest

from gopyt.check import check_package, load_package
from gopyt.diag import CompileError
from gopyt.fmt import fmt_module
from gopyt.parser import parse_module
from gopyt.testing import write_pkg

ALPHABET = string.printable
TOKEN_CAP = 20_000

VALID = [
    "module math\n\nfn add(left: i64, right: i64) -> i64\n",
    "module math\n\nfn add(left: i64, right: i64) -> i64\n{\n    return left + right\n}\n",
    (
        "module orders\n\nuse core.log { write }\n\n"
        "task ping() -> unit\n    effects { log }\n{\n"
        '    return core.log.write("x")\n}\n'
    ),
    (
        "module orders\n\ntype Order {\n    id: i64\n}\n\n"
        "enum Status {\n    Pending\n    Paid {\n        amount: i64\n    }\n}\n"
    ),
    (
        "module orders\n\nfn amount(status: Status) -> i64\n{\n"
        "    return match status {\n        Status.Pending -> 0\n"
        "        Status.Paid { amount } -> amount\n    }\n}\n"
    ),
    (
        "module orders\n\ntask pair() -> list[i64]\n    effects { time }\n{\n"
        "    return parallel max 2 timeout_ms 1000 {\n        1\n        2\n    }\n}\n"
    ),
    (
        "module orders\n\nuse core.list { range }\n\n"
        "task loop() -> i64\n    effects { time }\n{\n"
        "    for step in core.list.range(0, 3) {\n        core.time.sleep_ms(0)\n    }\n"
        "    return 0\n}\n"
    ),
]


def parse_only(src: str, role: str = "impl") -> None:
    """Parse and format. Only CompileError may escape."""
    module = parse_module(src, "impl/fuzz.gopyt", role)
    fmt_module(module)


class RandomBytes(unittest.TestCase):
    def test_random_text_never_crashes(self) -> None:
        random.seed(20260905)
        for i in range(220):
            length = random.randrange(0, 200)
            src = "".join(random.choice(ALPHABET) for _ in range(length))
            if len(src) > TOKEN_CAP:
                continue
            try:
                parse_only(src)
            except CompileError:
                pass
            except RecursionError:
                self.fail(f"recursion on case {i}: {src!r}")
            except Exception as exc:  # noqa: BLE001 - that is the point
                self.fail(f"case {i} raised {type(exc).__name__}: {exc}\n{src!r}")

    def test_random_tokens_never_crash(self) -> None:
        """Shuffled real tokens hit the parser rather than dying in the lexer."""
        random.seed(4242)
        pieces = [
            "module", "use", "fn", "task", "->", "{", "}", "(", ")", "[", "]", ",", ":",
            "i64", "str", "match", "if", "else", "for", "in", "return", "effects", "log",
            "orders", "value", "Payment", "some", "none", "unit", "true", "parallel",
            "max", "timeout_ms", "1", "0", '"text"', "requires", "ensures", "|", "?",
        ]
        for i in range(260):
            src = " ".join(random.choice(pieces) for _ in range(random.randrange(1, 40)))
            try:
                parse_only(src)
            except CompileError:
                pass
            except RecursionError:
                self.fail(f"recursion on case {i}: {src!r}")
            except Exception as exc:  # noqa: BLE001
                self.fail(f"case {i} raised {type(exc).__name__}: {exc}\n{src!r}")


class MutatedModules(unittest.TestCase):
    def mutate(self, src: str, rng: random.Random) -> str:
        kind = rng.randrange(6)
        if not src:
            return src
        cut = rng.randrange(len(src))
        if kind == 0:
            return src[:cut] + src[cut + 1 :]  # delete a character
        if kind == 1:
            return src[:cut] + rng.choice(ALPHABET) + src[cut:]  # insert
        if kind == 2:
            return src[:cut] + rng.choice(ALPHABET) + src[cut + 1 :]  # replace
        if kind == 3:
            return src[:cut]  # truncate
        if kind == 4:
            return src + src[cut:]  # duplicate a tail
        lines = src.split("\n")
        rng.shuffle(lines)
        return "\n".join(lines)

    def test_mutated_valid_modules_never_crash(self) -> None:
        random.seed(1312)
        rng = random.Random(99)
        for i in range(140):
            src = self.mutate(VALID[i % len(VALID)], rng)
            try:
                parse_only(src)
            except CompileError:
                pass
            except RecursionError:
                self.fail(f"recursion on case {i}: {src!r}")
            except Exception as exc:  # noqa: BLE001
                self.fail(f"case {i} raised {type(exc).__name__}: {exc}\n{src!r}")

    def test_mutated_packages_reach_the_checker(self) -> None:
        """Full pipeline: a mutated impl must give a diagnostic, never a crash."""
        random.seed(777)
        rng = random.Random(31337)
        spec = "module math\n\nfn add(left: i64, right: i64) -> i64\n"
        base = "module math\n\nfn add(left: i64, right: i64) -> i64\n{\n    return left + right\n}\n"
        for i in range(60):
            td = tempfile.mkdtemp()
            try:
                write_pkg(td, {"spec/math.gopyt": spec, "impl/math.gopyt": self.mutate(base, rng)},
                          lock=False)
                try:
                    check_package(load_package(td), check_fmt=False, check_lockfile=False)
                except CompileError:
                    pass
                except RecursionError:
                    self.fail(f"recursion on case {i}")
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"case {i} raised {type(exc).__name__}: {exc}")
            finally:
                shutil.rmtree(td, ignore_errors=True)


class Idempotence(unittest.TestCase):
    """Whatever survives the parser must have a formatting fixed point."""

    def test_mutants_that_parse_format_to_a_fixed_point(self) -> None:
        rng = random.Random(20260906)
        mutator = MutatedModules()
        parsed = 0
        for i in range(600):
            src = VALID[i % len(VALID)]
            for _ in range(rng.randrange(1, 4)):
                src = mutator.mutate(src, rng)
            try:
                once = fmt_module(parse_module(src, "impl/fuzz.gopyt", "impl"))
            except CompileError:
                continue
            except RecursionError:
                self.fail(f"recursion on case {i}: {src!r}")
            parsed += 1
            twice = fmt_module(parse_module(once, "impl/fuzz.gopyt", "impl"))
            self.assertEqual(once, twice, f"case {i}: {src!r}")
        self.assertGreater(parsed, 20)


class Limits(unittest.TestCase):
    """implementer.md 7 caps must be diagnostics, not stack overflows."""

    def test_deep_nesting_is_a_diagnostic(self) -> None:
        src = "module math\n\nfn deep() -> i64\n{\n    return " + "(" * 400 + "1" + ")" * 400 + "\n}\n"
        with self.assertRaises(CompileError) as cm:
            parse_only(src)
        self.assertIn(cm.exception.diag.code, (1, 11))

    def test_long_identifier_is_e010(self) -> None:
        name = "a" * 65
        with self.assertRaises(CompileError) as cm:
            parse_only(f"module math\n\nfn {name}() -> i64\n")
        self.assertEqual(cm.exception.diag.code, 10)

    def test_oversized_file_is_e010(self) -> None:
        with self.assertRaises(CompileError) as cm:
            parse_only("module math\n" + "// pad\n" * 200_000)
        self.assertEqual(cm.exception.diag.code, 10)


if __name__ == "__main__":
    unittest.main()
