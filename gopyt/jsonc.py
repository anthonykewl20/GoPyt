"""The one canonical JSON encoding (docs/stdlib.md, docs/security.md).

There is no second encoding and no user hook. Decode is exact: unknown key,
missing non-optional field, duplicate key, or trailing input is a ConvertError.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from gopyt import gobyte
from gopyt.gobyte import (
    TE_BOOL,
    TE_BYTES,
    TE_F64,
    TE_I32,
    TE_I64,
    TE_LIST,
    TE_MAP,
    TE_NOM,
    TE_OPT,
    TE_STR,
    TE_U32,
    TE_U64,
    TE_UNION,
    TE_UNIT,
    Artifact,
)
from gopyt.values import NONE, UNIT, EnumVal, NoneValue, Record, Some, Unit
from gopyt.values import I32, U32, U64

INT_VALUE = {TE_I32: I32, TE_I64: int, TE_U32: U32, TE_U64: U64}

INT_RANGE = {
    TE_I32: (-(2**31), 2**31 - 1),
    TE_I64: (-(2**63), 2**63 - 1),
    TE_U32: (0, 2**32 - 1),
    TE_U64: (0, 2**64 - 1),
}


class ConvertFail(Exception):
    def __init__(self, message: str = "convert") -> None:
        self.message = message
        super().__init__(message)


class NotJson(Exception):
    pass


def _pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ConvertFail("duplicate key")
        out[k] = v
    return out


def parse(text: str):
    def invalid_constant(value):
        raise ConvertFail("number")

    dec = json.JSONDecoder(object_pairs_hook=_pairs, parse_float=Decimal,
                           parse_constant=invalid_constant)
    try:
        value, end = dec.raw_decode(text, len(text) - len(text.lstrip(" \t\r\n")))
    except (ValueError, InvalidOperation, RecursionError):
        raise ConvertFail("syntax")
    if text[end:].strip(" \t\r\n") != "":
        raise ConvertFail("trailing")
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                raise ConvertFail("utf8")
        elif isinstance(item, dict):
            pending.extend(item)
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return value


def encode(art: Artifact, value: object, te_ix: int) -> str:
    return _enc(art, value, te_ix)


def _enc(art: Artifact, value: object, te_ix: int) -> str:
    te = art.texprs[te_ix]
    tag = te.tag
    if tag in (TE_F64, TE_BYTES):
        raise NotJson()
    if tag == TE_BOOL:
        if not isinstance(value, bool):
            raise ConvertFail("bool")
        return "true" if value else "false"
    if tag in INT_RANGE:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConvertFail("int")
        lo, hi = INT_RANGE[tag]
        if not (lo <= value <= hi):
            raise ConvertFail("range")
        return str(value)
    if tag == TE_STR:
        if not isinstance(value, str):
            raise ConvertFail("str")
        return json.dumps(value, ensure_ascii=False)
    if tag == TE_UNIT:
        return "{}"
    if tag == TE_OPT:
        if isinstance(value, NoneValue):
            return "null"
        if not isinstance(value, Some):
            raise ConvertFail("optional")
        return _enc(art, value.value, te.a)
    if tag == TE_LIST:
        if not isinstance(value, list):
            raise ConvertFail("list")
        return "[" + ",".join(_enc(art, v, te.a) for v in value) + "]"
    if tag == TE_MAP:
        if art.texprs[te.a].tag != TE_STR:
            raise NotJson()
        if not isinstance(value, dict):
            raise ConvertFail("map")
        keys = sorted(value, key=lambda k: k.encode("utf-8"))
        inner = ",".join(json.dumps(k, ensure_ascii=False) + ":" + _enc(art, value[k], te.b) for k in keys)
        return "{" + inner + "}"
    if tag == TE_NOM:
        return _enc_nom(art, value, te.a)
    if tag == TE_UNION:
        for mem in te.members:
            if _matches(art, value, mem):
                name = _member_name(art, mem)
                return "{" + json.dumps(name, ensure_ascii=False) + ":" + _enc(art, value, mem) + "}"
        raise ConvertFail("union")
    raise NotJson()


def _enc_nom(art: Artifact, value: object, type_id: int) -> str:
    td = art.types[type_id]
    if td.kind == 3:
        raise NotJson()
    if td.kind == 1:
        if not isinstance(value, Record) or value.type_id != type_id:
            raise ConvertFail("record")
        parts = []
        for (fname, fty), fval in zip(td.fields, value.fields):
            key = art.const_str(fname)
            parts.append(json.dumps(key, ensure_ascii=False) + ":" + _enc(art, fval, fty))
        return "{" + ",".join(parts) + "}"
    if not isinstance(value, EnumVal) or value.type_id != type_id:
        raise ConvertFail("enum")
    vname, fields = td.variants[value.variant]
    parts = []
    for (fname, fty), fval in zip(fields, value.fields):
        parts.append(json.dumps(art.const_str(fname), ensure_ascii=False) + ":" + _enc(art, fval, fty))
    body = "{" + ",".join(parts) + "}"
    return "{" + json.dumps(art.const_str(vname), ensure_ascii=False) + ":" + body + "}"


def _member_name(art: Artifact, te_ix: int) -> str:
    te = art.texprs[te_ix]
    if te.tag == TE_NOM:
        return art.const_str(art.types[te.a].name)
    for name, tag in (
        ("bool", TE_BOOL),
        ("i32", TE_I32),
        ("i64", TE_I64),
        ("u32", TE_U32),
        ("u64", TE_U64),
        ("str", TE_STR),
        ("bytes", TE_BYTES),
        ("unit", TE_UNIT),
        ("f64", TE_F64),
    ):
        if te.tag == tag:
            return name
    raise NotJson()


def _matches(art: Artifact, value: object, te_ix: int) -> bool:
    te = art.texprs[te_ix]
    if te.tag == TE_NOM:
        return isinstance(value, (Record, EnumVal)) and value.type_id == te.a
    if te.tag == TE_BOOL:
        return isinstance(value, bool)
    if te.tag in INT_RANGE:
        return type(value) is INT_VALUE[te.tag]
    if te.tag == TE_STR:
        return isinstance(value, str)
    if te.tag == TE_BYTES:
        return isinstance(value, bytes)
    if te.tag == TE_UNIT:
        return isinstance(value, Unit)
    return False


def decode(art: Artifact, text: str, te_ix: int) -> object:
    try:
        return _dec(art, parse(text), te_ix)
    except RecursionError:
        raise ConvertFail("depth")


def _dec(art: Artifact, data: object, te_ix: int) -> object:
    te = art.texprs[te_ix]
    tag = te.tag
    if tag in (TE_F64, TE_BYTES):
        raise NotJson()
    if tag == TE_BOOL:
        if not isinstance(data, bool):
            raise ConvertFail("bool")
        return data
    if tag in INT_RANGE:
        value = _as_int(data)
        lo, hi = INT_RANGE[tag]
        if not (lo <= value <= hi):
            raise ConvertFail("range")
        return INT_VALUE[tag](value)
    if tag == TE_STR:
        if not isinstance(data, str):
            raise ConvertFail("str")
        return data
    if tag == TE_UNIT:
        if data != {}:
            raise ConvertFail("unit")
        return UNIT
    if tag == TE_OPT:
        if data is None:
            return NONE
        return Some(_dec(art, data, te.a))
    if tag == TE_LIST:
        if not isinstance(data, list):
            raise ConvertFail("list")
        return [_dec(art, v, te.a) for v in data]
    if tag == TE_MAP:
        if art.texprs[te.a].tag != TE_STR:
            raise NotJson()
        if not isinstance(data, dict):
            raise ConvertFail("map")
        return {k: _dec(art, v, te.b) for k, v in data.items()}
    if tag == TE_NOM:
        return _dec_nom(art, data, te.a)
    if tag == TE_UNION:
        if not isinstance(data, dict) or len(data) != 1:
            raise ConvertFail("union")
        key = next(iter(data))
        for mem in te.members:
            if _member_name(art, mem) == key:
                return _dec(art, data[key], mem)
        raise ConvertFail("union member")
    raise NotJson()


def _as_int(data: object) -> int:
    if isinstance(data, bool):
        raise ConvertFail("int")
    if isinstance(data, int):
        return data
    if isinstance(data, Decimal) and data.is_finite() and data == data.to_integral_value():
        if data < -(2**63) or data > 2**64 - 1:
            raise ConvertFail("range")
        return int(data)
    raise ConvertFail("int")


def _dec_nom(art: Artifact, data: object, type_id: int) -> object:
    td = art.types[type_id]
    if td.kind == 3:
        raise NotJson()
    if not isinstance(data, dict):
        raise ConvertFail("object")
    if td.kind == 1:
        names = [art.const_str(fn) for fn, _ft in td.fields]
        for key in data:
            if key not in names:
                raise ConvertFail("unknown key")
        fields = []
        for (fname, fty), name in zip(td.fields, names):
            if name in data:
                fields.append(_dec(art, data[name], fty))
            elif art.texprs[fty].tag == TE_OPT:
                fields.append(NONE)
            else:
                raise ConvertFail("missing field")
        return Record(type_id, fields)
    if len(data) != 1:
        raise ConvertFail("enum")
    key = next(iter(data))
    for i, (vname, vfields) in enumerate(td.variants):
        if art.const_str(vname) != key:
            continue
        body = data[key]
        if not isinstance(body, dict):
            raise ConvertFail("enum payload")
        names = [art.const_str(fn) for fn, _ft in vfields]
        for k in body:
            if k not in names:
                raise ConvertFail("unknown key")
        fields = []
        for (fname, fty), name in zip(vfields, names):
            if name in body:
                fields.append(_dec(art, body[name], fty))
            elif art.texprs[fty].tag == TE_OPT:
                fields.append(NONE)
            else:
                raise ConvertFail("missing field")
        return EnumVal(type_id, i, fields)
    raise ConvertFail("variant")
