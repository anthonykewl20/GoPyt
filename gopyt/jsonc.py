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


class AllocationLimit(Exception):
    """Canonical text would exceed the VM per-value allocation limit."""



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


class _Encoder:
    def __init__(self, max_bytes, check_context=None, max_depth=None, *, text=False):
        import io
        self.max_bytes = max_bytes
        self.check_context = check_context
        self.max_depth = max_depth
        self.text = text
        self.size = 0
        self.output = io.StringIO() if text else bytearray()

    def check(self):
        if self.check_context is not None:
            self.check_context()

    def minimum(self, size):
        if size > self.max_bytes - self.size:
            raise ConvertFail('size')

    def token(self, text):
        raw = None
        try:
            self.check()
            try:
                raw = text.encode('utf-8')
            except UnicodeEncodeError as error:
                raise ConvertFail('utf8') from error
            self.minimum(len(raw))
            if self.text:
                self.output.write(text)
            else:
                self.output.extend(raw)
            self.size += len(raw)
        finally:
            raw = None
            text = None

    def string(self, text):
        # Bound escaping/UTF-8 temporaries even for a single enormous string.
        self.minimum(len(text) + 2)
        self.token('"')
        for offset in range(0, len(text), 4096):
            self.token(json.dumps(text[offset:offset + 4096], ensure_ascii=False)[1:-1])
        self.token('"')

    def result(self):
        self.check()
        return self.output.getvalue() if self.text else bytes(self.output)

    def close(self):
        if self.text:
            self.output.close()
        else:
            self.output.clear()


def encode(art: Artifact, value: object, te_ix: int) -> str:
    from gopyt import ops
    output = _Encoder(ops.MAX_ALLOC, text=True)
    try:
        _emit(art, value, te_ix, output, 0)
        return output.result()
    except ConvertFail as error:
        if error.message == 'size':
            raise AllocationLimit() from error
        raise
    finally:
        output.close()


def encode_bytes(art: Artifact, value: object, te_ix: int, *, max_bytes: int,
                 max_depth: int = 128, check_context=None) -> bytes:
    if type(max_bytes) is not int or max_bytes < 0 or type(max_depth) is not int or max_depth < 1:
        raise ValueError('JSON encoding bounds')
    output = _Encoder(max_bytes, check_context, max_depth)
    try:
        _emit(art, value, te_ix, output, 0)
        return output.result()
    except RecursionError as error:
        raise ConvertFail('depth') from error
    finally:
        output.close()


def encode_owned_bytes(art, value, te_ix, *, budget, max_bytes, max_depth=128,
                       check_context=None, member=None):
    """Return an internal byte owner; callers must close it after consumption."""
    from gopyt.resource_bytes import ByteBuilder
    if type(max_bytes) is not int or max_bytes < 0 or type(max_depth) is not int or max_depth < 1:
        raise ValueError('JSON encoding bounds')
    if member is not None and type(member) is not str:
        raise ValueError('JSON envelope member')

    class OwnedEncoder(_Encoder):
        def token(self, text):
            try:
                self.check()
                length = sum(1 if ord(c) < 0x80 else 2 if ord(c) < 0x800
                             else 3 if ord(c) < 0x10000 else 4 for c in text)
                self.minimum(length)
                try:
                    self.output.append_text(text)
                except UnicodeEncodeError as error:
                    raise ConvertFail('utf8') from error
                self.size += length
            finally:
                text = None

        def close(self):
            self.output.close()

    output = OwnedEncoder(max_bytes, check_context, max_depth)
    output.output = ByteBuilder(budget)
    try:
        if member is not None:
            output.token('{')
            output.string(member)
            output.token(':')
        _emit(art, value, te_ix, output, 0 if member is None else 1)
        if member is not None:
            output.token('}')
        output.check()
        return output.output.finish()
    except RecursionError as error:
        raise ConvertFail('depth') from error
    finally:
        output.close()


def _emit(art, value, te_ix, out, depth):
    out.check()
    if out.max_depth is not None and depth > out.max_depth:
        raise ConvertFail('depth')
    te = art.texprs[te_ix]
    tag = te.tag
    if (out.max_depth is not None and depth >= out.max_depth
            and tag in (TE_LIST, TE_MAP, TE_NOM, TE_UNION)):
        raise ConvertFail('depth')
    if tag in (TE_F64, TE_BYTES):
        raise NotJson()
    if tag == TE_BOOL:
        if not isinstance(value, bool):
            raise ConvertFail('bool')
        out.token('true' if value else 'false')
    elif tag in INT_RANGE:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConvertFail('int')
        lo, hi = INT_RANGE[tag]
        if not lo <= value <= hi:
            raise ConvertFail('range')
        out.token(str(value))
    elif tag == TE_STR:
        if not isinstance(value, str):
            raise ConvertFail('str')
        out.string(value)
    elif tag == TE_UNIT:
        out.token('{}')
    elif tag == TE_OPT:
        if isinstance(value, NoneValue):
            out.token('null')
        elif isinstance(value, Some):
            _emit(art, value.value, te.a, out, depth + 1)
        else:
            raise ConvertFail('optional')
    elif tag == TE_LIST:
        if not isinstance(value, list):
            raise ConvertFail('list')
        out.minimum(max(2, 2 * len(value) + 1))
        out.token('[')
        for index, item in enumerate(value):
            if index:
                out.token(',')
            _emit(art, item, te.a, out, depth + 1)
        out.token(']')
    elif tag == TE_MAP:
        if art.texprs[te.a].tag != TE_STR:
            raise NotJson()
        if not isinstance(value, dict):
            raise ConvertFail('map')
        # Before sorting, bound key references and UTF-8 sort-key allocation.
        minimum = max(2, 5 * len(value) + 1)
        out.minimum(minimum)
        for key in value:
            out.check()
            if not isinstance(key, str):
                raise ConvertFail('map key')
            minimum += len(key)
            out.minimum(minimum)
        try:
            keys = sorted(value, key=lambda key: key.encode('utf-8'))
        except UnicodeEncodeError as error:
            raise ConvertFail('utf8') from error
        out.check()
        out.token('{')
        for index, key in enumerate(keys):
            if index:
                out.token(',')
            out.string(key)
            out.token(':')
            _emit(art, value[key], te.b, out, depth + 1)
        out.token('}')
    elif tag == TE_NOM:
        _emit_nom(art, value, te.a, out, depth)
    elif tag == TE_UNION:
        for member in te.members:
            if _matches(art, value, member):
                out.token('{')
                out.string(_member_name(art, member))
                out.token(':')
                _emit(art, value, member, out, depth + 1)
                out.token('}')
                return
        raise ConvertFail('union')
    else:
        raise NotJson()


def _emit_nom(art, value, type_id, out, depth):
    td = art.types[type_id]
    if td.kind == 3:
        raise NotJson()
    if td.kind == 1:
        if not isinstance(value, Record) or value.type_id != type_id:
            raise ConvertFail('record')
        fields = td.fields
    else:
        if not isinstance(value, EnumVal) or value.type_id != type_id:
            raise ConvertFail('enum')
        vname, fields = td.variants[value.variant]
        if out.max_depth is not None and depth + 1 >= out.max_depth:
            raise ConvertFail('depth')
        out.token('{')
        out.string(art.const_str(vname))
        out.token(':')
        depth += 1
    out.token('{')
    for index, ((fname, fty), item) in enumerate(zip(fields, value.fields)):
        if index:
            out.token(',')
        out.string(art.const_str(fname))
        out.token(':')
        _emit(art, item, fty, out, depth + 1)
    out.token('}')
    if td.kind != 1:
        out.token('}')


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
        if isinstance(value, bool) or not isinstance(value, int):
            return False
        declared = next((kind for kind in (I32, U32, U64)
                         if isinstance(value, kind)), int)
        return declared is INT_VALUE[te.tag]
    if te.tag == TE_STR:
        return isinstance(value, str)
    if te.tag == TE_BYTES:
        return isinstance(value, bytes)
    if te.tag == TE_UNIT:
        return isinstance(value, Unit)
    return False


def decode(art: Artifact, text: str, te_ix: int, *, budget=None,
           check=lambda: None) -> object:
    if budget is not None:
        from gopyt.resource_json import parse_owned, decode_value
        data = None
        depth_error = False
        try:
            data = parse_owned(text, budget, check)
            return decode_value(art, data, te_ix, budget, check)
        except RecursionError:
            depth_error = True
        finally:
            text = data = None
        if depth_error:
            raise ConvertFail("depth")
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
