"""Strict JSON-lines transport; all order transitions run in compiled GoPyT."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

from gopyt.cli import build
from gopyt.values import Record
from gopyt.vm import VM

COMMAND_KEYS = ("op", "quantity", "unit_cents", "member", "expected_version")
SNAPSHOT_KEYS = (
    "outcome", "status", "version", "stock", "quantity", "refunded_quantity",
    "net_cents", "tax_cents", "shipping_cents", "charged_cents", "refunded_cents",
)


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


def parse_request(value: object) -> tuple[int, list[list[object]]]:
    if type(value) is not dict or set(value) != {"stock", "commands"}:
        raise ValueError("expected exactly stock and commands")
    stock, commands = value["stock"], value["commands"]
    if type(stock) is not int or not 0 <= stock <= 10_000:
        raise ValueError("stock must be an integer in 0..10000")
    if type(commands) is not list or len(commands) > 100:
        raise ValueError("commands must be a list of at most 100 entries")
    fields: list[list[object]] = []
    for command in commands:
        if type(command) is not dict or set(command) != set(COMMAND_KEYS):
            raise ValueError("expected exactly the five command fields")
        if type(command["op"]) is not str or type(command["member"]) is not bool:
            raise ValueError("op must be a string and member must be boolean")
        command["op"].encode("utf-8", "strict")
        for key in ("quantity", "unit_cents", "expected_version"):
            item = command[key]
            if type(item) is not int or not -1_000_000_000 <= item <= 1_000_000_000:
                raise ValueError("command numbers must be integers in transport range")
        fields.append([command[key] for key in COMMAND_KEYS])
    return stock, fields


class OrderAdapter:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else Path(__file__).resolve().parent
        _program, artifact, ids = build(str(self.root))
        self.vm = VM(artifact, root=str(self.root))
        self.target = ids["orders.service.run"]
        self.command_type = self.vm.type_id_of("orders.model.Command")
        self.request_type = self.vm.type_id_of("orders.model.Request")
        self.reply_type = self.vm.type_id_of("orders.model.Reply")
        self.snapshot_type = self.vm.type_id_of("orders.model.Snapshot")

    def run(self, value: object) -> dict[str, object]:
        stock, commands = parse_request(value)
        request = Record(self.request_type, [stock, [Record(self.command_type, fields)
                                                   for fields in commands]])
        try:
            reply = self.vm.call(self.target, [request])
            if not isinstance(reply, Record) or reply.type_id != self.reply_type or len(reply.fields) != 1:
                raise TypeError("service must return the declared Reply")
            results = []
            for snapshot in reply.fields[0]:
                if (not isinstance(snapshot, Record) or snapshot.type_id != self.snapshot_type
                        or len(snapshot.fields) != len(SNAPSHOT_KEYS)):
                    raise TypeError("service must return declared Snapshots")
                results.append(dict(zip(SNAPSHOT_KEYS, snapshot.fields, strict=True)))
            return {"results": results}
        finally:
            self.vm.heap.release_result()


def main() -> None:
    adapter = OrderAdapter()
    for line in sys.stdin:
        reply = adapter.run(decode_line(line))
        print(json.dumps(reply, separators=(",", ":"), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
