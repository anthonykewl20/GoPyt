"""GoPyT v0 stdlib declarations, copied verbatim from docs/stdlib.md.

The toolchain ships these modules (implementer.md 15); they are not app files.
tools/check_stdlib_sync.py asserts this file still matches the document.
"""

from __future__ import annotations

STDLIB_SOURCE: dict[str, str] = {
    'core.status': """module core.status

type NotFound {
}

type ConvertError {
    message: str
}

type IoError {
    message: str
}

type DbError {
    message: str
}

type ModelError {
    message: str
}

type ListenError {
    message: str
}

type HttpError {
    message: str
}

type TestFailed {
    message: str
}

type Throttled {
}
""",
    'core.list': """module core.list

fn empty[T]() -> list[T]

fn len[T](items: list[T]) -> i64
    ensures result >= 0

fn get[T](items: list[T], index: i64) -> T?
    requires index >= 0

fn append[T](items: list[T], item: T) -> list[T]

fn range(start: i64, end: i64) -> list[i64]
    requires start <= end
""",
    'core.map': """module core.map

fn empty[K, V]() -> map[K, V]

fn len[K, V](map_value: map[K, V]) -> i64
    ensures result >= 0

fn get[K, V](map_value: map[K, V], key: K) -> V?

fn set[K, V](map_value: map[K, V], key: K, value: V) -> map[K, V]

fn keys[K, V](map_value: map[K, V]) -> list[K]
""",
    'core.str': """module core.str

use core.status { ConvertError }

fn len(text: str) -> i64
    ensures result >= 0

fn concat(left: str, right: str) -> str

fn from_i64(value: i64) -> str

fn slice(text: str, start: i64, end: i64) -> str | ConvertError
    requires start >= 0
    requires end >= start
""",
    'core.bytes': """module core.bytes

use core.status { ConvertError }

fn len(data: bytes) -> i64
    ensures result >= 0

fn from_str(text: str) -> bytes

fn to_str(data: bytes) -> str | ConvertError

fn concat(left: bytes, right: bytes) -> bytes
""",
    'core.int': """module core.int

use core.status { ConvertError }

fn to_i64_from_i32(value: i32) -> i64
fn to_i64_from_u32(value: u32) -> i64
fn to_i64_from_u64(value: u64) -> i64 | ConvertError
fn to_i32(value: i64) -> i32 | ConvertError
fn to_u32(value: i64) -> u32 | ConvertError
fn to_u64(value: i64) -> u64 | ConvertError
""",
    'core.convert': """module core.convert

use core.status { ConvertError }

trait FromStr {
    fn from_str(text: str) -> Self | ConvertError
}

trait Json {
    fn to_json(value: Self) -> str | ConvertError
    fn from_json(text: str) -> Self | ConvertError
}

provide FromStr for i64
provide FromStr for bool
provide FromStr for str

provide Json for bool
provide Json for i32
provide Json for i64
provide Json for u32
provide Json for u64
provide Json for str
provide Json for unit
""",
    'core.test': """module core.test

fn assert_eq[T](left: T, right: T) -> unit
""",
    'core.log': """module core.log

task write(message: str) -> unit
    effects { log }
""",
    'core.time': """module core.time

task now_ms() -> i64
    effects { time }

task sleep_ms(ms: i64) -> unit
    effects { time }
    requires ms >= 0
""",
    'core.random': """module core.random

task i64_in(min: i64, max: i64) -> i64
    effects { random }
    requires min <= max
    ensures result >= min
    ensures result <= max
""",
    'core.file': """module core.file

use core.status { IoError, NotFound }

task read(path: str) -> bytes | NotFound | IoError
    effects { filesystem.read }

task write(path: str, data: bytes) -> unit | IoError
    effects { filesystem.write }
""",
    'core.model': """module core.model

use core.status { ModelError }

task complete(prompt: str) -> str | ModelError
    effects { network, model }

task local(prompt: str, max_tokens: i64) -> str | ModelError
    effects { model }
    requires max_tokens >= 1 and max_tokens <= 64
""",
    'core.secret': """module core.secret

use core.status { NotFound }
use core.str { len }

task get(name: str) -> Secret | NotFound
    effects { secret }
    requires core.str.len(name) > 0

task reveal(value: Secret) -> str
    effects { secret }
""",
    'core.observe': """module core.observe

use core.str { len }

type Report {
    events: i64
    fail: i64
    mean_ms: i64
    var_ms: i64
    cusum_alarm: bool
}

task report() -> Report
    effects { observe }

task note(tag: str) -> unit
    effects { observe }
    requires core.str.len(tag) > 0
""",
    'core.limit': """module core.limit

use core.status { Throttled }

task allow(key: str, tokens: i64, refill_ms: i64) -> unit | Throttled
    effects { time }
    requires tokens > 0
    requires refill_ms > 0
""",
    'core.evolve': """module core.evolve

type NoChange {
}

type EvolveError {
    message: str
}

type Applied {
    digest: str
}

task propose() -> Applied | NoChange | EvolveError
    effects { model, time, log, observe, filesystem.read, filesystem.write }
""",
    'net.http': """module net.http

use core.status { HttpError, ListenError }
use core.str { len }

enum HttpMethod {
    Get
    Post
    Put
    Patch
    Delete
}

type HttpRequest {
    method: HttpMethod
    url: str
    body: bytes
}

type HttpResponse {
    status: i64
    body: bytes
}

task request(req: HttpRequest) -> HttpResponse | HttpError
    effects { network }
    requires core.str.len(req.url) > 0

task serve() -> unit | ListenError
    effects { network }
""",
    'data.json': """module data.json

use core.status { ConvertError }

fn encode[T](value: T) -> str | ConvertError

fn decode[T](text: str) -> T | ConvertError
""",
    'store.db': """module store.db

use core.convert { Json }
use core.status { DbError, NotFound }
use core.str { len }

task get(key: str) -> str | NotFound | DbError
    effects { database.read, ffi }
    requires core.str.len(key) > 0

task put(key: str, value: str) -> unit | DbError
    effects { database.write, ffi }
    requires core.str.len(key) > 0

task compare_exchange(key: str, expected: str?, value: str) -> bool | DbError
    effects { database.read, database.write, ffi }
    requires core.str.len(key) > 0


type Change {
    key: str
    expected: str?
    value: str?
}

type Snapshot {
    values: list[str?]
}

provide Json for Change

provide Json for Snapshot

task get_many(keys: list[str]) -> Snapshot | DbError
    effects { database.read, ffi }

task compare_exchange_many(changes: list[Change]) -> bool | DbError
    effects { database.read, database.write, ffi }
""",
}

STDLIB_MODULES = tuple(STDLIB_SOURCE)
