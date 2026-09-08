"""Strict JSON-lines transport; every multi-line order transition runs in compiled GoPyT."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

from gopyt.cli import build
from gopyt.values import Record
from gopyt.vm import VM

COMMAND_KEYS = ("op", "lines", "sku", "quantity", "expected_version", "day", "coupon", "tier")
LINE_INPUT_KEYS = ("sku", "quantity", "unit_cents", "category")
SNAPSHOT_KEYS = (
    "outcome", "status", "version", "day_placed", "tier", "subtotal_cents", "discount_cents",
    "tax_cents", "shipping_cents", "charged_cents", "refunded_cents", "lines", "stock",
)
LINE_KEYS = ("sku", "quantity", "refunded_quantity", "net_cents", "tax_cents", "refunded_cents")
SKU_PATTERN = re.compile(r"[A-Za-z0-9]{1,16}")


def _integer(token: str) -> int:
    if re.fullmatch(r"0|-?[1-9][0-9]*", token) is None or len(token) > 11:
        raise ValueError("unsafe integer spelling or range")
    value = int(token)
    if not -1_000_000_000 <= value <= 1_000_000_000:
        raise ValueError("integer outside transport range")
    return value


def _reject_number(token: str) -> object:
    raise ValueError("only canonical integer numbers are accepted")


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def decode_line(line: str) -> object:
    return json.loads(line, parse_int=_integer, parse_float=_reject_number,
                      parse_constant=_reject_number, object_pairs_hook=_object)


def _string(value: object, what: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{what} must be a string")
    value.encode("utf-8", "strict")
    return value


def _transport_int(value: object, what: str) -> int:
    if type(value) is not int or not -1_000_000_000 <= value <= 1_000_000_000:
        raise ValueError(f"{what} must be an integer in transport range")
    return value


def parse_line(value: object) -> list[object]:
    if type(value) is not dict or set(value) != set(LINE_INPUT_KEYS):
        raise ValueError("expected exactly the four line fields")
    return [_string(value["sku"], "line sku"), _transport_int(value["quantity"], "line quantity"),
            _transport_int(value["unit_cents"], "line unit_cents"), _string(value["category"], "line category")]


def parse_request(value: object) -> tuple[dict[str, int], list[list[object]]]:
    if type(value) is not dict or set(value) != {"stock", "commands"}:
        raise ValueError("expected exactly stock and commands")
    stock, commands = value["stock"], value["commands"]
    if type(stock) is not dict or not 1 <= len(stock) <= 8:
        raise ValueError("stock must be an object of 1..8 entries")
    for sku, count in stock.items():
        if SKU_PATTERN.fullmatch(sku) is None:
            raise ValueError("stock key must be 1..16 ASCII letters or digits")
        if type(count) is not int or not 0 <= count <= 10_000:
            raise ValueError("stock value must be an integer in 0..10000")
    if type(commands) is not list or len(commands) > 100:
        raise ValueError("commands must be a list of at most 100 entries")
    fields: list[list[object]] = []
    for command in commands:
        if type(command) is not dict or set(command) != set(COMMAND_KEYS):
            raise ValueError("expected exactly the eight command fields")
        lines = command["lines"]
        if type(lines) is not list or len(lines) > 5:
            raise ValueError("lines must be a list of at most 5 line objects")
        fields.append([
            _string(command["op"], "op"),
            [parse_line(line) for line in lines],
            _string(command["sku"], "sku"),
            _transport_int(command["quantity"], "quantity"),
            _transport_int(command["expected_version"], "expected_version"),
            _transport_int(command["day"], "day"),
            _string(command["coupon"], "coupon"),
            _string(command["tier"], "tier"),
        ])
    return dict(stock), fields


class OrderAdapter:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else Path(__file__).resolve().parent
        _program, artifact, ids = build(str(self.root))
        self.vm = VM(artifact, root=str(self.root))
        self.target = ids["orders_multi.service.run"]
        self.line_input_type = self.vm.type_id_of("orders_multi.model.LineInput")
        self.command_type = self.vm.type_id_of("orders_multi.model.Command")
        self.request_type = self.vm.type_id_of("orders_multi.model.Request")
        self.reply_type = self.vm.type_id_of("orders_multi.model.Reply")
        self.snapshot_type = self.vm.type_id_of("orders_multi.model.Snapshot")
        self.line_type = self.vm.type_id_of("orders_multi.model.Line")

    def _line(self, line: object) -> dict[str, object]:
        if not isinstance(line, Record) or line.type_id != self.line_type or len(line.fields) != len(LINE_KEYS):
            raise TypeError("service must return declared Lines")
        return dict(zip(LINE_KEYS, line.fields, strict=True))

    def _snapshot(self, snapshot: object) -> dict[str, object]:
        if (not isinstance(snapshot, Record) or snapshot.type_id != self.snapshot_type
                or len(snapshot.fields) != len(SNAPSHOT_KEYS)):
            raise TypeError("service must return declared Snapshots")
        row = dict(zip(SNAPSHOT_KEYS, snapshot.fields, strict=True))
        if not isinstance(row["lines"], list) or not isinstance(row["stock"], dict):
            raise TypeError("service must return declared line list and stock map")
        row["lines"] = [self._line(line) for line in row["lines"]]
        row["stock"] = {sku: row["stock"][sku] for sku in sorted(row["stock"])}
        return row

    def run(self, value: object) -> dict[str, object]:
        stock, commands = parse_request(value)
        request = Record(self.request_type, [stock, [
            Record(self.command_type, [fields[0], [Record(self.line_input_type, line) for line in fields[1]],
                                       *fields[2:]])
            for fields in commands]])
        try:
            reply = self.vm.call(self.target, [request])
            if not isinstance(reply, Record) or reply.type_id != self.reply_type or len(reply.fields) != 1:
                raise TypeError("service must return the declared Reply")
            return {"results": [self._snapshot(snapshot) for snapshot in reply.fields[0]]}
        finally:
            self.vm.heap.release_result()


def main() -> None:
    adapter = OrderAdapter()
    for line in sys.stdin:
        reply = adapter.run(decode_line(line))
        print(json.dumps(reply, separators=(",", ":"), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
