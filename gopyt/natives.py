"""kind-6 stdlib natives (docs/stdlib.md, docs/implementer.md 12-15).

Every host interaction in the language happens here and nowhere else. A native
enforces its own declared `requires` because kind-6 functions carry no code.
"""

from __future__ import annotations

import os
import secrets as _secrets
import sys
import time
import urllib.error
import urllib.request

from gopyt import gobyte, jsonc, ops
from gopyt.gobyte import TE_UNION, Artifact, Func
from gopyt.jsonc import ConvertFail, NotJson
from gopyt.values import NONE, UNIT, EnumVal, Record, Secret, Some
from gopyt.values import I32 as I32Value, U32 as U32Value, U64 as U64Value
from gopyt.vm import Trap, Cancelled

NATIVES: dict[str, object] = {}

I32 = (-(2**31), 2**31 - 1)
U32 = (0, 2**32 - 1)
U64 = (0, 2**64 - 1)
I64 = (-(2**63), 2**63 - 1)

HTTP_TIMEOUT_MS = 30_000
MAX_BODY = 8_388_608
MAX_REQUEST_BODY = 1_048_576
MAX_HANDLERS = 64


def resource_denial(vm, name, args):
    """Uniform host authority admission before native I/O or cache access."""
    if vm.authority is None:
        return None
    requests = []
    error = None
    if name.startswith('store.db.'):
        operation = name.removeprefix('store.db.')
        if operation in ('get_many', 'compare_exchange_many'):
            from gopyt.storage import MAX_BATCH_KEYS
            if not 1 <= len(args[0]) <= MAX_BATCH_KEYS:
                return _status(vm, 'DbError', 'database batch requires 1..256 keys')
        keys = (args[0] if operation == 'get_many' else
                [row.fields[0] for row in args[0]] if operation == 'compare_exchange_many'
                else [args[0]])
        if operation != 'put':
            requests.extend(('database_read', key) for key in keys)
        if operation not in ('get', 'get_many'):
            requests.extend(('database_write', key) for key in keys)
        error = 'DbError'
    elif name in ('core.file.read', 'core.file.write'):
        requests = [('file_' + name.rsplit('.', 1)[1], args[0])]
        error = 'IoError'
    elif name == 'core.secret.get':
        if not vm.allows_resources(('secrets', args[0])):
            vm.observe.deny('resource', context=vm)
            return _status(vm, 'NotFound')
    elif name == 'core.model.local':
        # Companion imports execute arbitrary Python and cannot inherit this
        # VM's mediation. Restricted VMs never enter that integration.
        return _status(vm, 'ModelError', 'resource authority: unmediated provider')
    elif name == 'core.evolve.propose':
        # Evolution writes source and launches host processes outside VM natives.
        return Record(vm.type_id_of('core.evolve.EvolveError'), ['resource authority: unmediated evolution'])
    if error is not None and not vm.allows_resources(*requests):
        vm.observe.deny('resource', context=vm)
        return _status(vm, error, 'resource authority denied')
    return None


def native(name: str):
    def wrap(fn):
        NATIVES[name] = fn
        return fn

    return wrap


def _requires(cond: bool) -> None:
    if not cond:
        raise Trap(ops.TRAP_REQUIRES)


def _status(vm, name: str, *fields) -> Record:
    return Record(vm.type_id_of("core.status." + name), list(fields))


def _convert_error(vm, message: str) -> Record:
    return _status(vm, "ConvertError", message)


def _ret_member(vm, func: Func, exclude: tuple[str, ...] = ()) -> int:
    """The payload texpr of a `T | SomeError` return type."""
    te = vm.art.texprs[func.ret]
    if te.tag != TE_UNION:
        return func.ret
    for mem in te.members:
        name = None
        m = vm.art.texprs[mem]
        if m.tag == gobyte.TE_NOM:
            name = vm.art.const_str(vm.art.types[m.a].name)
        if name is None or not name.startswith("core.status."):
            return mem
    return te.members[0]


# ---------------------------------------------------------------- core.list


@native("core.list.empty")
def _list_empty(vm, args, func):
    return []


@native("core.list.len")
def _list_len(vm, args, func):
    return len(args[0])


@native("core.list.get")
def _list_get(vm, args, func):
    items, index = args
    _requires(index >= 0)
    if index >= len(items):
        return NONE
    return Some(items[index])


@native("core.list.append")
def _list_append(vm, args, func):
    items, item = args
    if len(items) + 1 > ops.MAX_ALLOC:
        raise Trap(ops.TRAP_ALLOC)
    return items + [item]


@native("core.list.range")
def _list_range(vm, args, func):
    start, end = args
    _requires(start <= end)
    if end - start > ops.MAX_ALLOC:
        raise Trap(ops.TRAP_ALLOC)
    return list(range(start, end))


# ---------------------------------------------------------------- core.map


@native("core.map.empty")
def _map_empty(vm, args, func):
    return {}


@native("core.map.len")
def _map_len(vm, args, func):
    return len(args[0])


@native("core.map.get")
def _map_get(vm, args, func):
    mp, key = args
    if key in mp:
        return Some(mp[key])
    return NONE


@native("core.map.set")
def _map_set(vm, args, func):
    mp, key, value = args
    if len(mp) + (key not in mp) > ops.MAX_ALLOC:
        raise Trap(ops.TRAP_ALLOC)
    out = dict(mp)
    out[key] = value
    return out


@native("core.map.keys")
def _map_keys(vm, args, func):
    keys = list(args[0])
    if keys and isinstance(keys[0], bool):
        return sorted(keys)
    if keys and isinstance(keys[0], str):
        return sorted(keys, key=lambda k: k.encode("utf-8"))
    return sorted(keys)


# ---------------------------------------------------------------- core.str


@native("core.str.len")
def _str_len(vm, args, func):
    return len(args[0])


@native("core.str.concat")
def _str_concat(vm, args, func):
    if sum(len(value.encode("utf-8")) for value in args) > ops.MAX_ALLOC:
        raise Trap(ops.TRAP_ALLOC)
    return args[0] + args[1]


@native("core.str.from_i64")
def _str_from_i64(vm, args, func):
    return str(args[0])


@native("core.str.slice")
def _str_slice(vm, args, func):
    text, start, end = args
    _requires(start >= 0)
    _requires(end >= start)
    if end > len(text):
        return _convert_error(vm, "slice")
    return text[start:end]


# ---------------------------------------------------------------- core.bytes


@native("core.bytes.len")
def _bytes_len(vm, args, func):
    return len(args[0])


@native("core.bytes.from_str")
def _bytes_from_str(vm, args, func):
    return args[0].encode("utf-8")


@native("core.bytes.to_str")
def _bytes_to_str(vm, args, func):
    try:
        return args[0].decode("utf-8")
    except UnicodeDecodeError:
        return _convert_error(vm, "utf8")


@native("core.bytes.concat")
def _bytes_concat(vm, args, func):
    if len(args[0]) + len(args[1]) > ops.MAX_ALLOC:
        raise Trap(ops.TRAP_ALLOC)
    return args[0] + args[1]


# ---------------------------------------------------------------- core.int


def _widen(vm, value, bounds, value_type=int):
    lo, hi = bounds
    if lo <= value <= hi:
        return value_type(value)
    return _convert_error(vm, "range")


NATIVES["core.int.to_i64_from_i32"] = lambda vm, args, func: int(args[0])
NATIVES["core.int.to_i64_from_u32"] = lambda vm, args, func: int(args[0])
NATIVES["core.int.to_i64_from_u64"] = lambda vm, args, func: _widen(vm, args[0], I64)
NATIVES["core.int.to_i32"] = lambda vm, args, func: _widen(vm, args[0], I32, I32Value)
NATIVES["core.int.to_u32"] = lambda vm, args, func: _widen(vm, args[0], U32, U32Value)
NATIVES["core.int.to_u64"] = lambda vm, args, func: _widen(vm, args[0], U64, U64Value)


# ---------------------------------------------------------------- core.test


@native("core.test.assert_eq")
def _assert_eq(vm, args, func):
    from gopyt.vm import value_eq

    if not value_eq(args[0], args[1]):
        raise Trap(ops.TRAP_ASSERT)
    return UNIT


# ---------------------------------------------------------------- effects


@native("core.log.write")
def _log_write(vm, args, func):
    message = args[0]
    if isinstance(message, Secret):
        vm.observe.deny("secret", context=vm)
        raise Trap(ops.TRAP_TYPE)
    sys.stderr.write(message + "\n")
    sys.stderr.flush()
    return UNIT


@native("core.time.now_ms")
def _now_ms(vm, args, func):
    try:
        ns = time.time_ns()
    except (OSError, OverflowError) as error:
        raise Trap(ops.TRAP_TYPE) from error
    if type(ns) is not int:
        raise Trap(ops.TRAP_TYPE)
    ms = ns // 1_000_000
    if not I64[0] <= ms <= I64[1]:
        raise Trap(ops.TRAP_OVERFLOW)
    return ms


@native("core.time.sleep_ms")
def _sleep_ms(vm, args, func):
    _requires(args[0] >= 0)
    # Integer deadlines preserve the entire nonnegative i64 input range.
    # Bounded host waits avoid platform timeout overflow and observe ancestors.
    deadline = time.monotonic_ns() + args[0] * 1_000_000
    while True:
        vm.check_cancelled()
        now = time.monotonic_ns()
        remaining = deadline - now
        if remaining <= 0:
            break
        if vm.deadline_ns is not None:
            remaining = min(remaining, max(0, vm.deadline_ns - now))
        time.sleep(min(remaining, 50_000_000) / 1_000_000_000)
    return UNIT


@native("core.random.i64_in")
def _random_i64(vm, args, func):
    lo, hi = args
    _requires(lo <= hi)
    return lo + _secrets.randbelow(hi - lo + 1)


def _safe_path(vm, path: str, write=False) -> str | None:
    if not path or path.startswith("/") or path.endswith("/") or "\\" in path or "\0" in path:
        return None
    segs = path.split("/")
    if any(s in ("", ".", "..") for s in segs):
        return None
    folded = path.casefold()
    if any(seg.casefold() in (".git", ".gopyt-state") for seg in segs):
        return None
    if write and (segs[0].casefold() in ("spec", "impl", "test", "build")
                  or folded in ("gopyt.toml", "gopyt.lock")
                  or folded.endswith((".gopyt", ".gobyte", ".py", ".pyc", ".so"))):
        return None
    root = os.path.realpath(vm.root)
    full = os.path.join(root, *segs)
    cur = root
    for seg in segs:
        cur = os.path.join(cur, seg)
        if os.path.islink(cur):
            return None
    if os.path.commonpath([root, os.path.abspath(full)]) != root:
        return None
    return full


@native("core.file.read")
def _file_read(vm, args, func):
    from gopyt.files import regular_file

    full = _safe_path(vm, args[0])
    if full is None:
        return _status(vm, "IoError", "path")
    try:
        with regular_file(vm.root, args[0], check_context=vm.check_cancelled, buffering=0) as fh:
            data = bytearray()
            while True:
                vm.check_cancelled()
                chunk = fh.read(min(65_536, ops.MAX_ALLOC + 1 - len(data)))
                vm.check_cancelled()
                if chunk is None:
                    raise OSError('file read would block')
                if not chunk:
                    return bytes(data)
                data.extend(chunk)
                if len(data) > ops.MAX_ALLOC:
                    raise Trap(ops.TRAP_ALLOC)
    except FileNotFoundError:
        return _status(vm, "NotFound")
    except OSError:
        return _status(vm, "IoError", "read")


@native("core.file.write")
def _file_write(vm, args, func):
    from gopyt.files import regular_file

    path, data = args
    full = _safe_path(vm, path, write=True)
    if full is None or not os.path.isdir(os.path.dirname(full)):
        return _status(vm, "IoError", "path")
    try:
        with regular_file(vm.root, path, write=True,
                          check_context=vm.check_cancelled, buffering=0) as fh:
            with memoryview(data) as view:
                offset = 0
                while offset < len(view):
                    vm.check_cancelled()
                    written = fh.write(view[offset:offset + 65_536])
                    if written is None or written <= 0:
                        raise OSError('file write made no progress')
                    offset += written
            vm.check_cancelled()
    except OSError:
        return _status(vm, "IoError", "write")
    return UNIT


@native("core.secret.get")
def _secret_get(vm, args, func):
    name = args[0]
    _requires(len(name) > 0)
    from gopyt.manifest import _is_snake

    if not _is_snake(name):
        return _status(vm, "NotFound")
    value = os.environ.get("GOPYT_SECRET_" + name.upper())
    if value is None:
        return _status(vm, "NotFound")
    return Secret(value, vm.type_id_of("core.secret.Secret"))


@native("core.secret.reveal")
def _secret_reveal(vm, args, func):
    value = args[0]
    if not isinstance(value, Secret):
        raise Trap(ops.TRAP_TYPE)
    return value.text


@native("core.observe.report")
def _observe_report(vm, args, func):
    obs = vm.observe
    with obs.admission(vm):
        fields = [min(2**63-1, obs.events), min(2**63-1, obs.fail),
                  min(2**63-1, int(obs.welford.mean)),
                  min(2**63-1, int(obs.welford.variance())), obs.cusum.alarm]
    return Record(
        vm.type_id_of("core.observe.Report"),
        fields,
    )


@native("core.observe.note")
def _observe_note(vm, args, func):
    tag = args[0]
    _requires(len(tag) > 0)
    vm.observe.note(tag, context=vm)
    return UNIT


@native("core.limit.allow")
def _limit_allow(vm, args, func):
    key, tokens, refill_ms = args
    _requires(tokens > 0)
    _requires(refill_ms > 0)
    with vm.native_admission():
        now = time.monotonic()
        allowed = vm.limiter.allow(key, tokens, refill_ms, now)
    if not allowed:
        vm.observe.deny("limit", context=vm)
        return _status(vm, "Throttled")
    return UNIT


@native("core.evolve.propose")
def _evolve_propose(vm, args, func):
    """docs/evolve.md. Quiet CUSUM -> NoChange without calling any model."""
    from gopyt import evolve as _evolve

    if not vm.observe.cusum.alarm:
        return Record(vm.type_id_of("core.evolve.NoChange"), [])
    module = vm.module_stack[-1] if vm.module_stack else ""
    bounds = vm.evolve_bounds(module)
    if bounds is None:
        return Record(vm.type_id_of("core.evolve.EvolveError"), ["no evolve block"])
    with vm.native_admission():
        if vm.evolve_in_flight:
            # Little's law (hardening.md): at most one propose in flight.
            return Record(vm.type_id_of("core.evolve.EvolveError"), ["in flight"])
        now = time.monotonic()
        if vm.evolve_last and (now - vm.evolve_last) * 1000.0 < bounds[1]:
            # Cooldown is the evolve block's timeout_ms (docs/hardening.md).
            return Record(vm.type_id_of("core.evolve.NoChange"), [])
        vm.evolve_last = now
        vm.evolve_in_flight = True
    try:
        outcome = _evolve.propose(vm.root, True, bounds[0], timeout_ms=bounds[1], module=module, traces=vm.observe.snapshot(context=vm), context=vm)
    except (Trap, Cancelled):
        raise
    except Exception as exc:  # a broken wave must not take the VM with it
        return Record(vm.type_id_of("core.evolve.EvolveError"), [type(exc).__name__])
    finally:
        vm.evolve_in_flight = False
    if outcome.kind == "NoChange":
        return Record(vm.type_id_of("core.evolve.NoChange"), [])
    if outcome.kind == "Applied":
        # Files and lock only: the loaded artifact is never patched (E116).
        return Record(vm.type_id_of("core.evolve.Applied"), [outcome.digest])
    return Record(vm.type_id_of("core.evolve.EvolveError"), [outcome.message])


# ---------------------------------------------------------------- store.db


@native("store.db.get")
def _db_get(vm, args, func):
    from gopyt.storage import StorageError
    key = args[0]
    _requires(len(key) > 0)
    try:
        value = vm.db.get(key)
        return _status(vm, "NotFound") if value is None else value
    except StorageError as exc:
        return _status(vm, "DbError", str(exc))


@native("store.db.put")
def _db_put(vm, args, func):
    from gopyt.storage import StorageError
    key, value = args
    _requires(len(key) > 0)
    try:
        vm.db.put(key, value)
        return UNIT
    except StorageError as exc:
        return _status(vm, "DbError", str(exc))


@native("store.db.compare_exchange")
def _db_compare_exchange(vm, args, func):
    from gopyt.storage import StorageError
    key, expected, value = args
    _requires(len(key) > 0)
    try:
        return vm.db.compare_exchange(key, expected.value if isinstance(expected, Some) else None, value)
    except StorageError as exc:
        return _status(vm, "DbError", str(exc))


# ---------------------------------------------------------------- data.json


@native("store.db.get_many")
def _db_get_many(vm, args, func):
    from gopyt.storage import StorageError
    try:
        values = vm.db.get_many(args[0])
        return Record(vm.type_id_of("store.db.Snapshot"),
                      [[NONE if value is None else Some(value) for value in values]])
    except StorageError as exc:
        return _status(vm, "DbError", str(exc))


@native("store.db.compare_exchange_many")
def _db_compare_exchange_many(vm, args, func):
    from gopyt.storage import StorageError, MAX_BATCH_KEYS
    if not 1 <= len(args[0]) <= MAX_BATCH_KEYS:
        return _status(vm, "DbError", "database batch requires 1..256 keys")
    changes = [(row.fields[0],
                row.fields[1].value if isinstance(row.fields[1], Some) else None,
                row.fields[2].value if isinstance(row.fields[2], Some) else None)
               for row in args[0]]
    try:
        return vm.db.compare_exchange_many(changes)
    except StorageError as exc:
        return _status(vm, "DbError", str(exc))


@native("data.json.encode")
def _json_encode(vm, args, func):
    param_te = func.params[0]
    try:
        return jsonc.encode(vm.art, args[0], param_te)
    except ConvertFail as e:
        return _convert_error(vm, e.message)
    except NotJson:
        return _convert_error(vm, "not json")


@native("data.json.decode")
def _json_decode(vm, args, func):
    te = _ret_member(vm, func)
    try:
        return jsonc.decode(vm.art, args[0], te)
    except ConvertFail as e:
        return _convert_error(vm, e.message)
    except NotJson:
        return _convert_error(vm, "not json")


# ---------------------------------------------------------------- net.http

METHOD_NAMES = ["Get", "Post", "Put", "Patch", "Delete"]


@native("net.http.request")
def _http_request(vm, args, func):
    req = args[0]
    method_val, url, body = req.fields
    method = METHOD_NAMES[method_val.variant] if isinstance(method_val, EnumVal) else "Get"
    _requires(len(url) > 0)
    module = vm.module_stack[-1] if vm.module_stack else ""
    allowed = set(vm.egress_for(module))
    from gopyt.check import normalize_origin

    origin = _origin(url)
    norm = normalize_origin(origin) if origin else None
    if norm is None or norm not in allowed or not vm.allows_resources(('network', norm)):
        vm.observe.deny("egress", context=vm)
        return _status(vm, "HttpError", "egress")
    if "@" in url.split("://", 1)[1].split("/", 1)[0]:
        return _status(vm, "HttpError", "userinfo")
    request = urllib.request.Request(url, data=body or None, method=method.upper())
    from gopyt.netio import Budget, opener
    budget = Budget(vm, HTTP_TIMEOUT_MS)
    try:
        try:
            resp = opener(budget, _NoRedirect()).open(request, timeout=budget.remaining())
        except urllib.error.HTTPError as error:
            resp = error
        with resp:
            data = resp.read(MAX_BODY + 1)
            status = resp.code if isinstance(resp, urllib.error.HTTPError) else resp.status
        budget.remaining()
    except (Trap, Cancelled):
        raise
    except Exception:
        return _status(vm, "HttpError", "network")
    if len(data) > MAX_BODY:
        return _status(vm, "HttpError", "body too large")
    return Record(vm.type_id_of("net.http.HttpResponse"), [status, data])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _origin(url: str) -> str | None:
    from gopyt.check import _origin_of

    return _origin_of(url)


@native("core.model.complete")
def _model_complete(vm, args, func):
    prompt = args[0]
    url = os.environ.get("GOPYT_MODEL_URL")
    if not url:
        return _status(vm, "ModelError", "no model url")
    from gopyt.check import normalize_origin

    module = vm.module_stack[-1] if vm.module_stack else ""
    origin = _origin(url)
    norm = normalize_origin(origin) if origin else None
    if (norm is None or norm not in set(vm.egress_for(module))
            or not vm.allows_resources(('network', norm))):
        vm.observe.deny("egress", context=vm)
        return _status(vm, "ModelError", "egress")
    import json as _json

    payload = _json.dumps({"prompt": prompt}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, method="POST", headers={"Content-Type": "application/json"}
    )
    from gopyt.netio import Budget, opener
    budget = Budget(vm, HTTP_TIMEOUT_MS)
    try:
        try:
            resp = opener(budget, _NoRedirect()).open(request, timeout=budget.remaining())
        except urllib.error.HTTPError as exc:
            exc.close()
            budget.remaining()
            return _status(vm, "ModelError", "status")
        with resp:
            budget.remaining()
            if not (200 <= resp.status < 300):
                return _status(vm, "ModelError", "status")
            raw = resp.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                return _status(vm, "ModelError", "body too large")
            data = raw.decode("utf-8")
        budget.remaining()
    except (Trap, Cancelled):
        raise
    except Exception:
        return _status(vm, "ModelError", "network")
    try:
        obj = jsonc.parse(data)
    except ConvertFail:
        return _status(vm, "ModelError", "decode")
    if not isinstance(obj, dict) or set(obj) != {"text"} or not isinstance(obj["text"], str):
        return _status(vm, "ModelError", "decode")
    return obj["text"]


@native("core.model.local")
def _model_local(vm, args, func):
    try:
        from peon.local import complete
        return complete(args[0], args[1])
    except (ImportError, ValueError, OSError, RuntimeError, UnicodeError) as exc:
        # Model errors are data, never an external-provider fallback.
        return _status(vm, "ModelError", str(exc)[:256])


@native("net.http.serve")
def _http_serve(vm, args, func):
    from gopyt.server import serve

    module = vm.module_stack[-1] if vm.module_stack else ""
    return serve(vm, module)


# ---------------------------------------------------------------- provides


def _provide_json(vm, args, func):
    return _json_encode(vm, args, func)


def _provide_from_json(vm, args, func):
    return _json_decode(vm, args, func)


def _provide_from_str(vm, args, func):
    return _from_str_value(vm, args[0], _ret_member(vm, func))


def _from_str_value(vm, text, te):
    expression = vm.art.texprs[te]
    tag = expression.tag
    if tag == gobyte.TE_NOM:
        definition = vm.art.types[expression.a]
        if definition.kind != 1 or len(definition.fields) != 1:
            return _convert_error(vm, "from_str")
        value = _from_str_value(vm, text, definition.fields[0][1])
        return value if isinstance(value, Record) else Record(expression.a, [value])
    if tag == gobyte.TE_STR:
        return text
    if tag == gobyte.TE_BOOL:
        if text in ("true", "false"):
            return text == "true"
        return _convert_error(vm, "bool")
    if tag == gobyte.TE_I64:
        body = text[1:] if text.startswith("-") else text
        if not body.isdigit() or not body.isascii():
            return _convert_error(vm, "i64")
        if len(body) > 1 and body[0] == "0":
            return _convert_error(vm, "i64")
        if len(body) > 19:
            return _convert_error(vm, "range")
        value = int(text)
        if not (I64[0] <= value <= I64[1]):
            return _convert_error(vm, "range")
        return value
    return _convert_error(vm, "from_str")


def resolve_provide(name: str):
    parts = name.split(":")
    if len(parts) != 4 or parts[0] != "provide":
        return None
    trait, _target, member = parts[1], parts[2], parts[3]
    if trait == "core.convert.Json":
        return {"to_json": _provide_json, "from_json": _provide_from_json}.get(member)
    if trait == "core.convert.FromStr" and member == "from_str":
        return _provide_from_str
    return None


class _NativeTable(dict):
    def get(self, key, default=None):  # type: ignore[override]
        hit = dict.get(self, key)
        if hit is not None:
            return hit
        hit = resolve_provide(key)
        if hit is not None:
            self[key] = hit
            return hit
        return default


from gopyt.money import install as _install_money

_install_money(NATIVES)
from gopyt.temporal import install as _install_time

_install_time(NATIVES)
NATIVES = _NativeTable(NATIVES)


# ---------------------------------------------------------------- verification

_DECLS: dict[str, tuple[int, int]] | None = None
_SIGS = None
_STDLIB_TYPES = None


def shipped_declarations() -> dict[str, tuple[int, int]]:
    """(arity, effect mask) for every stdlib callable, from docs/stdlib.md."""
    global _DECLS
    if _DECLS is None:
        from gopyt.check import Checker, Pkg, load_stdlib

        pkg = Pkg(root=".")
        pkg.stdlib = load_stdlib()
        ck = Checker(pkg)
        ck.collect()
        ck.resolve_bodies_of_types()
        _DECLS = {
            name: (len(sig.params), ops.effect_mask(sig.effects))
            for name, sig in ck.sigs.items()
        }
    return _DECLS


def artifact_type(art: Artifact, index: int):
    """Rebuild a Ty from an artifact TypeExpr, for native verification."""
    from gopyt import types as T

    te = art.texprs[index]
    prims = {
        gobyte.TE_BOOL: "bool",
        gobyte.TE_I32: "i32",
        gobyte.TE_I64: "i64",
        gobyte.TE_U32: "u32",
        gobyte.TE_U64: "u64",
        gobyte.TE_F64: "f64",
        gobyte.TE_STR: "str",
        gobyte.TE_BYTES: "bytes",
        gobyte.TE_UNIT: "unit",
    }
    if te.tag in prims:
        return T.Prim(prims[te.tag])
    if te.tag == gobyte.TE_OPT:
        return T.Opt(artifact_type(art, te.a))
    if te.tag == gobyte.TE_LIST:
        return T.ListT(artifact_type(art, te.a))
    if te.tag == gobyte.TE_MAP:
        return T.MapT(artifact_type(art, te.a), artifact_type(art, te.b))
    if te.tag == gobyte.TE_NOM:
        name = art.const_str(art.types[te.a].name)
        return T.Nom(name)
    if te.tag == gobyte.TE_UNION:
        return T.Union(tuple(artifact_type(art, m) for m in te.members))
    raise gobyte.e100()


def shipped_signatures():
    """name -> the stdlib Sig, for type verification at load time."""
    global _SIGS, _STDLIB_TYPES
    if _SIGS is None:
        from gopyt.check import Checker, Pkg, load_stdlib

        pkg = Pkg(root=".")
        pkg.stdlib = load_stdlib()
        ck = Checker(pkg)
        ck.collect()
        ck.resolve_bodies_of_types()
        _SIGS = dict(ck.sigs)
        _STDLIB_TYPES = dict(ck.types)
    return _SIGS


def verify_natives(art: Artifact) -> None:
    """bytecode.md: a kind-6 entry must match the shipped declaration."""
    from gopyt.types import unify

    decls = shipped_declarations()
    sigs = shipped_signatures()
    for td in art.types:
        name = art.const_str(td.name)
        if name.split(".", 1)[0] not in ("core", "net", "data", "store"):
            continue
        declared = _STDLIB_TYPES.get(name)
        if declared is None or td.kind != {"record": 1, "enum": 2, "opaque": 3}[declared.kind]:
            raise gobyte.e100()

        def same_fields(actual, expected):
            return (len(actual) == len(expected) and all(
                art.const_str(name) == want_name and artifact_type(art, index) == want_type
                for (name, index), (want_name, want_type) in zip(actual, expected)
            ))

        if not same_fields(td.fields, declared.fields) or len(td.variants) != len(declared.variants):
            raise gobyte.e100()
        for (name, fields), (want_name, want_fields) in zip(td.variants, declared.variants):
            if art.const_str(name) != want_name or not same_fields(fields, want_fields):
                raise gobyte.e100()
    for f in art.funcs:
        if f.kind != ops.KIND_NATIVE:
            continue
        name = art.const_str(f.name)
        if NATIVES.get(name) is None:
            raise gobyte.e100()
        if name.startswith("provide:") and name not in sigs:
            from gopyt.types import Nom, Prim, STR, Union

            _prefix, trait, target, member = name.split(":")
            builtin = target in ("bool", "i32", "i64", "u32", "u64", "str", "unit")
            if trait == "core.convert.Json" and member in ("to_json", "from_json"):
                pass
            elif trait == "core.convert.FromStr" and member == "from_str":
                if target not in ("bool", "i64", "str"):
                    definitions = [td for td in art.types if art.const_str(td.name) == target]
                    if (len(definitions) != 1 or definitions[0].kind != 1
                            or len(definitions[0].fields) != 1
                            or art.texprs[definitions[0].fields[0][1]].tag not in (gobyte.TE_STR, gobyte.TE_I64)):
                        raise gobyte.e100()
            else:
                raise gobyte.e100()
            if f.arity != 1 or f.effects != 0:
                raise gobyte.e100()
            target_type = Prim(target) if builtin else Nom(target)
            want_param = target_type if member == "to_json" else STR
            want_result = STR if member == "to_json" else target_type
            want_ret = Union((want_result, Nom("core.status.ConvertError")))
            if (artifact_type(art, f.params[0]) != want_param
                    or artifact_type(art, f.ret) != want_ret):
                raise gobyte.e100()
            if not builtin and not any(art.const_str(td.name) == target and td.kind in (1, 2) for td in art.types):
                raise gobyte.e100()
            continue
        decl = decls.get(name)
        if decl is None or decl[0] != f.arity or decl[1] != f.effects:
            raise gobyte.e100()
        sig = sigs.get(name)
        if sig is None:
            raise gobyte.e100()
        env = {}
        if sig.self_ty is not None:
            env["Self"] = sig.self_ty
        for (_pname, declared), index in zip(sig.params, f.params):
            if not unify(declared, artifact_type(art, index), env):
                raise gobyte.e100()
        # The return type may carry the only occurrence of a type variable
        # (`core.list.empty[T]() -> list[T]`), so unify rather than compare.
        if not unify(sig.ret, artifact_type(art, f.ret), env):
            raise gobyte.e100()
