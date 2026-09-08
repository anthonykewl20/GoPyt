"""Public examples, separate from the independently authored acceptance oracle."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from adapter import OrderAdapter, decode_line


def command(op: str, version: int, quantity: int = 0, price: int = 0,
            member: bool = False) -> dict[str, object]:
    return {"op": op, "quantity": quantity, "unit_cents": price,
            "member": member, "expected_version": version}


class PublicExamples(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="gopyt-orders-public-")
        cls.root = Path(cls.temporary.name) / "orders"
        shutil.copytree(Path(__file__).resolve().parent, cls.root,
                        ignore=shutil.ignore_patterns("build", "__pycache__", ".gopyt*"))
        cls.adapter = OrderAdapter(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def run_commands(self, commands: list[dict[str, object]], stock: int = 10) -> list[dict[str, object]]:
        return self.adapter.run({"stock": stock, "commands": commands})["results"]

    def test_empty_and_maximum_length(self) -> None:
        self.assertEqual(self.run_commands([]), [])
        results = self.run_commands([command("unknown", 0)] * 100)
        self.assertEqual(len(results), 100)
        self.assertTrue(all(result["outcome"] == "invalid_command" for result in results))

    def test_member_quote_and_full_lifecycle(self) -> None:
        results = self.run_commands([
            command("place", 0, 3, 1201, True), command("pay", 1), command("ship", 2),
            command("refund", 3, 1), command("refund", 4, 2),
        ])
        self.assertEqual(results[0], {
            "outcome": "ok", "status": "reserved", "version": 1, "stock": 7,
            "quantity": 3, "refunded_quantity": 0, "net_cents": 3423,
            "tax_cents": 282, "shipping_cents": 500, "charged_cents": 0,
            "refunded_cents": 0,
        })
        self.assertEqual(results[1]["charged_cents"], 4205)
        self.assertEqual(results[3]["refunded_cents"], 1235)
        self.assertEqual(results[-1], {
            "outcome": "ok", "status": "refunded", "version": 5, "stock": 10,
            "quantity": 3, "refunded_quantity": 3, "net_cents": 3423,
            "tax_cents": 282, "shipping_cents": 500, "charged_cents": 4205,
            "refunded_cents": 3705,
        })

    def test_paid_and_reserved_cancellation(self) -> None:
        for pay in (False, True):
            commands = [command("place", 0, 2, 100)]
            if pay:
                commands.append(command("pay", 1))
            commands.append(command("cancel", len(commands)))
            final = self.run_commands(commands)[-1]
            self.assertEqual(final["status"], "cancelled")
            self.assertEqual(final["stock"], 10)
            self.assertEqual(final["refunded_cents"], 716 if pay else 0)
            self.assertEqual(final["refunded_quantity"], 2 if pay else 0)

    def test_failure_preserves_entire_state(self) -> None:
        results = self.run_commands([
            command("place", 0, 2, 100), command("refund", 9, -1),
            command("nonsense", 9), command("refund", 1, -1),
        ])
        for result, outcome in zip(results[1:], ("conflict", "invalid_command", "invalid_state")):
            self.assertEqual(result, results[0] | {"outcome": outcome})

    def test_zero_price_still_charges_shipping(self) -> None:
        results = self.run_commands([command("place", 0, 1, 0), command("pay", 1)])
        self.assertEqual(results[-1]["charged_cents"], 500)

    def test_transport_shapes_and_types(self) -> None:
        valid = {"stock": 10, "commands": [command("place", 0, 2, 100)]}
        invalid = [None, [], {}, {"stock": 10, "commands": [], "extra": 1},
                   {"stock": True, "commands": []}, {"stock": 10001, "commands": []},
                   {"stock": 0, "commands": [command("pay", 0)] * 101}]
        for key, bad_value in (("quantity", True), ("quantity", 1.0),
                               ("quantity", 1_000_000_001), ("member", 1), ("op", 1)):
            request = copy.deepcopy(valid)
            request["commands"][0][key] = bad_value
            invalid.append(request)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.adapter.run(value)

    def test_unsafe_json_numbers_and_duplicate_keys(self) -> None:
        for number in ("-0", "1.0", "1e0", "NaN", "Infinity", "1000000001", "01"):
            with self.subTest(number=number), self.assertRaises(ValueError):
                decode_line('{"stock":' + number + ',"commands":[]}')
        with self.assertRaises(ValueError):
            decode_line('{"stock":0,"stock":1,"commands":[]}')

    def test_json_lines_fails_without_business_reply(self) -> None:
        process = subprocess.run([sys.executable, str(self.root / "adapter.py")],
                                 input='{"stock":true,"commands":[]}\n',
                                 text=True, capture_output=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(process.stdout, "")

    def test_json_lines_multiple_scenarios(self) -> None:
        process = subprocess.run([sys.executable, str(self.root / "adapter.py")],
                                 input='{"stock":0,"commands":[]}\n' * 2,
                                 text=True, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual([json.loads(line) for line in process.stdout.splitlines()],
                         [{"results": []}, {"results": []}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
