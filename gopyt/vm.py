"""The v0 bytecode VM (docs/bytecode.md).

A stack machine. Host interaction exists only through verified kind-6 stdlib
natives; there is no opcode for FFI, eval, or unstructured concurrency.
"""

from __future__ import annotations

import struct
import math
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field

from gopyt import gobyte, ops
from gopyt.gobyte import Artifact, Func
from gopyt.observe import Observe
from gopyt.values import NONE, UNIT, EnumVal, NoneValue, Record, Secret, Some, Unit
from gopyt.resource_buffer import Buffer, BufferView
from gopyt.values import I32, U32, U64

I64_MIN = -(2**63)
I64_MAX = 2**63 - 1


class Trap(Exception):
    def __init__(self, code: int) -> None:
        self.code = code
        self.observed = False  # recorded once, at the frame that raised it
        super().__init__(f"trap {code}")


class Cancelled(Exception):
    pass


@dataclass
class Frame:
    func: Func
    fn_id: int
    locals: list
    init: set
    stack: list = field(default_factory=list)


class ResourceCleanupError(RuntimeError):
    """Retains the VM so an embedding caller can inspect and retry cleanup."""
    def __init__(self, vm):
        super().__init__('VM resource cleanup incomplete')
        self.vm = vm


class VM:
    def __init__(self, art: Artifact, root: str | None = None, *, authority=None, identities=None, parallel_workers: int = 64, resource_budget=None) -> None:
        from gopyt.toolchain import FINGERPRINT
        if art.toolchain != FINGERPRINT:
            raise gobyte.e100()
        for constant in art.consts:
            if constant.tag == gobyte.TAG_F64 and (type(constant.value) is not float
                                                  or not math.isfinite(constant.value)):
                raise gobyte.e100()
        import sys
        from gopyt.limiter import Limiter
        from gopyt.heap import Heap
        from gopyt.storage import Store
        from gopyt.resource_authority import ResourceAuthority

        if authority is not None and type(authority) is not ResourceAuthority:
            raise TypeError('authority must be a host ResourceAuthority handle')
        self._authority = authority
        from gopyt.identity import SessionBroker
        if identities is not None and (type(identities) is not SessionBroker
                or authority is None or not identities.authority.is_descendant_of(authority)):
            raise TypeError('identity broker requires a descendant of the VM authority')
        self.identities = identities

        if sys.getrecursionlimit() < 8192:
            sys.setrecursionlimit(8192)

        from gopyt.scheduling import ParallelBudget
        self.parallel_budget = ParallelBudget(parallel_workers)
        from gopyt.resource_budget import ResourceBudget, ResourceLimits
        if resource_budget is not None and type(resource_budget) is not ResourceBudget:
            raise TypeError("resource_budget must be a host ResourceBudget")
        self.resource_budget = resource_budget if resource_budget is not None else ResourceBudget(
            ResourceLimits(256 * 1024 * 1024, 512 * 1024 * 1024, 256, 65536))
        from gopyt.resource_descriptors import DescriptorRegistry
        self.descriptors = DescriptorRegistry(self.resource_budget)
        self._lifecycle_lock = threading.Lock()
        self._active_calls = 0
        self._closed = False
        self.heap = Heap(c.value for c in art.consts)
        self.art = art
        self.root = "." if root is None else root
        self._tl = threading.local()
        from secrets import token_hex
        self.clock_id = token_hex(16)
        self.observe = Observe(reservoir_k=32)
        self.db = Store(root, context=self)
        self.limiter = Limiter()
        self.buckets = self.limiter.buckets
        self.serving = False
        self.evolve_in_flight = False
        self.evolve_agents = {art.const_str(e.module):
                              (e.max_candidates, e.timeout_ms, e.reservoir)
                              for e in art.evolve}
        if self.evolve_agents:
            self.set_reservoir(max(v[2] for v in self.evolve_agents.values()))
        self.evolve_last: float = 0.0
        self.lock = threading.Lock()
        self.names = [art.const_str(f.name) for f in art.funcs]
        self.by_name: dict[str, int] = {}
        for i, name in enumerate(self.names):
            self.by_name.setdefault(name, i)
        from gopyt.natives import NATIVES, verify_natives

        self.natives = NATIVES
        verify_natives(art)
        if identities is not None:
            identities._bind()

    # -- helpers ---------------------------------------------------------

    @property
    def authority(self):
        return getattr(self._tl, 'authority', self._authority)

    @contextmanager
    def authority_scope(self, authority):
        """Host-only attenuation for one call tree, inherited by task children."""
        from gopyt.resource_authority import ResourceAuthority, AuthorityError
        previous = self.authority
        if (type(authority) is not ResourceAuthority
                or previous is not None and not authority.is_descendant_of(previous)):
            raise AuthorityError('scope requires a descendant authority')
        self._tl.authority = authority
        try:
            yield
        finally:
            self._tl.authority = previous

    def allows_resources(self, *requests):
        authority = self.authority
        return authority is None or authority.admits(requests)

    @property
    def request_identity(self):
        return getattr(self._tl, 'identity', None)

    @contextmanager
    def request_scope(self, session):
        from gopyt.identity import AuthenticatedRequest
        if type(session) is not AuthenticatedRequest:
            raise TypeError('verified request context required')
        previous = self.request_identity
        with self.authority_scope(session.authority):
            self._tl.identity = session.identity
            try:
                yield
            finally:
                self._tl.identity = previous

    @property
    def depth(self) -> int:
        return getattr(self._tl, "depth", 0)

    @depth.setter
    def depth(self, value: int) -> None:
        self._tl.depth = value

    @property
    def cancels(self) -> tuple:
        """Cancellation events of the PARALLEL frames this thread runs under.

        Per-thread rather than per-VM, so one arm's trap cannot stop an
        unrelated `parallel` or an HTTP handler running beside it.
        """
        return getattr(self._tl, "cancels", ())

    @cancels.setter
    def cancels(self, value: tuple) -> None:
        self._tl.cancels = value

    @property
    def module_stack(self) -> list[str]:
        stack = getattr(self._tl, "modules", None)
        if stack is None:
            stack = []
            self._tl.modules = stack
        return stack

    def type_id_of(self, name: str) -> int:
        for i, td in enumerate(self.art.types):
            if self.art.const_str(td.name) == name:
                return i
        raise gobyte.e100()

    def type_name(self, type_id: int) -> str:
        return self.art.const_str(self.art.types[type_id].name)

    def module_of(self, fn_id: int) -> str:
        name = self.names[fn_id]
        while name.startswith("arm:"):
            name = name[4:].rsplit(":", 1)[0]
        if name.startswith("provide:"):
            # Orphan rules place a provide in its target type's module.
            name = name.split(":")[2]
        name = name.split("[", 1)[0]
        return name.rsplit(".", 1)[0]

    def egress_for(self, module: str) -> list[str]:
        out = []
        for entry in self.art.egress:
            if self.art.const_str(entry.module) == module:
                out.append(self.art.const_str(entry.origin))
        return out

    def set_reservoir(self, size: int) -> None:
        """Size the Vitter reservoir from the agent's `evolve { reservoir K }`."""
        if size > 0 and size != self.observe.reservoir_k:
            self.observe = Observe(reservoir_k=size)

    def evolve_bounds(self, module: str) -> tuple[int, int, int] | None:
        """`evolve { max N timeout_ms M reservoir K }` of the calling module.

        The version-2 artifact carries the checked per-module bounds.
        """
        return self.evolve_agents.get(module)

    def routes_for(self, module: str):
        return [r for r in self.art.routes if self.art.const_str(r.module) == module]

    @property
    def deadline_ns(self) -> int | None:
        return getattr(self._tl, 'deadline_ns', None)

    @deadline_ns.setter
    def deadline_ns(self, value: int | None) -> None:
        if value is not None and type(value) is not int:
            raise TypeError('deadline_ns must be an integer or None')
        self._tl.deadline_ns = value

    def check_cancelled(self) -> None:
        if any(cancel.is_set() for cancel in self.cancels):
            raise Cancelled()
        deadline = self.deadline_ns
        if deadline is not None:
            import time
            if time.monotonic_ns() >= deadline:
                raise Trap(ops.TRAP_TIMEOUT)

    @contextmanager
    def native_admission(self, *, lock=None):
        """Wait cooperatively for shared native state, then check admission."""
        import time
        lock = self.lock if lock is None else lock
        while True:
            self.check_cancelled()
            remaining = .05
            if self.deadline_ns is not None:
                remaining = min(remaining, max(0, self.deadline_ns - time.monotonic_ns()) / 1_000_000_000)
            if lock.acquire(timeout=remaining):
                break
        try:
            self.check_cancelled()
            yield
        finally:
            lock.release()

    # -- calling ---------------------------------------------------------

    def __enter__(self):
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError('VM is closed')
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if not self.close():
            raise ResourceCleanupError(self)
        return False

    def close(self):
        """Close owned resources only when no calls are active; never wait on calls.

        Returns false for a busy VM or unfinished physical cleanup. Once idle close
        begins, new calls reject and the host may retry cleanup until it succeeds.
        Embedding pins preserve objects, not permission to use a closed VM.
        """
        with self._lifecycle_lock:
            if self._active_calls:
                return False
            self._closed = True
        self.heap.release_all_results()
        self.heap.collect()
        self.heap.close_resources()
        descriptors_closed = self.descriptors.close()
        return self.heap.pending_resources() == 0 and descriptors_closed

    def call(self, fn_id: int, args: list, caller_effects: int | None = None) -> object:
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError('VM is closed')
            self._active_calls += 1
        try:
            return self._call_admitted(fn_id, args, caller_effects)
        finally:
            with self._lifecycle_lock:
                self._active_calls -= 1

    def _call_admitted(self, fn_id: int, args: list, caller_effects: int | None = None) -> object:
        import time
        started = time.monotonic()
        func = self.art.funcs[fn_id]
        boundary = self.depth == 0 or func.kind == ops.KIND_NATIVE
        if boundary:
            require_finite_values(args, resource_heap=self.heap)
        with self.heap.pin(args):
            result = self._call(fn_id, args, caller_effects)
            self.check_cancelled()
            if boundary:
                require_finite_values(args, resource_heap=self.heap)
                require_finite_values(result, resource_heap=self.heap)
            result = self.heap.handoff(result)
            if not self.names[fn_id].startswith("core.observe.") and (func.kind in (ops.KIND_TASK, ops.KIND_WORKFLOW) or (func.kind == ops.KIND_NATIVE and func.effects)):
                tag = self.type_name(result.type_id) if isinstance(result, (Record, EnumVal, Secret, Buffer, BufferView)) else type(result).__name__
                if isinstance(result, EnumVal):
                    tag += "." + self.art.const_str(self.art.types[result.type_id].variants[result.variant][0])
                try:
                    self.observe.outcome(self.names[fn_id], tag, (time.monotonic() - started) * 1000, context=self)
                except BaseException:
                    self.heap.release_result()
                    raise
            return result

    def _call(self, fn_id: int, args: list, caller_effects: int | None = None) -> object:
        self.check_cancelled()
        func = self.art.funcs[fn_id]
        if caller_effects is not None and func.effects & ~ops.FFI_BIT & ~caller_effects:
            raise Trap(ops.TRAP_EFFECT)
        if len(args) != func.arity:
            raise Trap(ops.TRAP_TYPE)
        self.depth += 1
        if self.depth > ops.MAX_CALL_DEPTH:
            self.depth -= 1
            raise Trap(ops.TRAP_DEPTH)
        try:
            self.observe.task(self.names[fn_id], context=self)
            if func.kind == ops.KIND_NATIVE:
                native = self.natives.get(self.names[fn_id])
                if native is None:
                    raise gobyte.e100()
                from gopyt.natives import resource_denial
                denial = resource_denial(self, self.names[fn_id], args)
                result = denial if denial is not None else native(self, args, func)
                self.check_cancelled()
                return result
            self.module_stack.append(self.module_of(fn_id))
            try:
                return self.exec(fn_id, func, args)
            finally:
                self.module_stack.pop()
        except Trap as t:
            # One event per trap, not one per frame it unwinds through.
            if not t.observed:
                t.observed = True
                self.observe.trap(t.code, self.names[fn_id], context=self)
            raise
        finally:
            self.depth -= 1

    def exec(self, fn_id: int, func: Func, args: list) -> object:
        frame = Frame(func, fn_id, [None] * func.nlocals, set())
        for i, a in enumerate(args):
            frame.locals[i] = a
            frame.init.add(i)
        with self.heap.frame(frame):
            return self._exec_frame(frame)

    def _exec_frame(self, frame):
        instruction_guard = self.heap.step(frame, quantum=32)
        try:
            return self._execute_frame(frame, instruction_guard)
        finally:
            instruction_guard.finish()

    def _execute_frame(self, frame, instruction_guard):
        func = frame.func
        code = func.code
        stack = frame.stack
        pc = 0
        n = len(code)
        while pc < n:
            with instruction_guard:
                self.check_cancelled()
                op = code[pc]
                pc += 1
                if op == ops.CONST:
                    ix = struct.unpack_from("<I", code, pc)[0]
                    pc += 4
                    stack.append(self.const_value(ix))
                elif op == ops.LOAD_LOCAL:
                    slot = struct.unpack_from("<H", code, pc)[0]
                    pc += 2
                    if slot not in frame.init:
                        raise Trap(ops.TRAP_TYPE)
                    stack.append(frame.locals[slot])
                elif op == ops.RESET_LOCAL:
                    slot = struct.unpack_from("<H", code, pc)[0]
                    pc += 2
                    frame.init.discard(slot)
                    frame.locals[slot] = None
                elif op == ops.STORE_LOCAL:
                    slot = struct.unpack_from("<H", code, pc)[0]
                    pc += 2
                    if slot in frame.init:
                        raise Trap(ops.TRAP_DOUBLE_STORE)
                    frame.locals[slot] = stack.pop()
                    frame.init.add(slot)
                elif op == ops.RETURN:
                    return self.heap.handoff(stack.pop())
                elif op == ops.POP:
                    stack.pop()
                elif op == ops.DUP:
                    stack.append(stack[-1])
                elif op == ops.UNIT:
                    stack.append(UNIT)
                elif op == ops.SOME:
                    stack.append(Some(stack.pop()))
                elif op == ops.NOP:
                    pass
                elif op == ops.HALT:
                    return UNIT
                elif op == ops.TRAP:
                    codeval = struct.unpack_from("<H", code, pc)[0]
                    raise Trap(codeval)
                elif op == ops.JUMP:
                    off = struct.unpack_from("<i", code, pc)[0]
                    pc = pc + 4 + off
                elif op == ops.JUMP_IF_FALSE:
                    off = struct.unpack_from("<i", code, pc)[0]
                    pc += 4
                    if stack.pop() is False:
                        pc += off
                elif op == ops.JUMP_IF_TRUE:
                    off = struct.unpack_from("<i", code, pc)[0]
                    pc += 4
                    if stack.pop() is True:
                        pc += off
                elif op == ops.GET_FIELD:
                    ix = struct.unpack_from("<H", code, pc)[0]
                    pc += 2
                    target = stack.pop()
                    if isinstance(target, Record):
                        fields = target.fields
                    elif isinstance(target, EnumVal):
                        fields = target.fields
                    else:
                        raise Trap(ops.TRAP_TYPE)
                    if ix >= len(fields):
                        raise Trap(ops.TRAP_TYPE)
                    stack.append(fields[ix])
                elif op == ops.NEW_RECORD:
                    type_id, count = struct.unpack_from("<IH", code, pc)
                    pc += 6
                    vals = stack[len(stack) - count :]
                    del stack[len(stack) - count :]
                    stack.append(Record(type_id, list(vals)))
                elif op == ops.NEW_ENUM:
                    type_id, variant, count = struct.unpack_from("<IHH", code, pc)
                    pc += 8
                    vals = stack[len(stack) - count :]
                    del stack[len(stack) - count :]
                    stack.append(EnumVal(type_id, variant, list(vals)))
                elif op == ops.ENUM_TAG:
                    v = stack.pop()
                    if not isinstance(v, EnumVal):
                        raise Trap(ops.TRAP_TYPE)
                    stack.append(v.variant)
                elif op == ops.VALUE_TYPE:
                    v = stack.pop()
                    if isinstance(v, (Record, EnumVal, Secret, Buffer, BufferView)):
                        stack.append(v.type_id)
                    else:
                        stack.append(scalar_type_id(v))
                elif op == ops.IS_NONE:
                    v = stack.pop()
                    stack.append(isinstance(v, NoneValue))
                elif op == ops.SOME_VALUE:
                    v = stack.pop()
                    if not isinstance(v, Some):
                        raise Trap(ops.TRAP_TYPE)
                    stack.append(v.value)
                elif op == ops.NEW_LIST:
                    count = struct.unpack_from("<H", code, pc)[0]
                    pc += 2
                    vals = stack[len(stack) - count :]
                    del stack[len(stack) - count :]
                    stack.append(list(vals))
                elif op == ops.LIST_LEN:
                    v = stack.pop()
                    if not isinstance(v, list):
                        raise Trap(ops.TRAP_TYPE)
                    stack.append(len(v))
                elif op == ops.LIST_GET:
                    idx = stack.pop()
                    lst = stack.pop()
                    if not isinstance(lst, list) or isinstance(idx, bool) or not isinstance(idx, int):
                        raise Trap(ops.TRAP_TYPE)
                    if idx < 0 or idx >= len(lst):
                        raise Trap(ops.TRAP_INDEX)
                    stack.append(lst[idx])
                elif op == ops.LIST_APPEND:
                    item = stack.pop()
                    lst = stack.pop()
                    if not isinstance(lst, list):
                        raise Trap(ops.TRAP_TYPE)
                    if len(lst) + 1 > ops.MAX_ALLOC:
                        raise Trap(ops.TRAP_ALLOC)
                    stack.append(lst + [item])
                elif op == ops.NEW_MAP:
                    stack.append({})
                elif op == ops.MAP_GET:
                    key = stack.pop()
                    mp = stack.pop()
                    if not isinstance(mp, dict):
                        raise Trap(ops.TRAP_TYPE)
                    hit = mp.get(map_key(key), _MISSING)
                    stack.append(NONE if hit is _MISSING else Some(hit))
                elif op == ops.MAP_SET:
                    value = stack.pop()
                    key = stack.pop()
                    mp = stack.pop()
                    if not isinstance(mp, dict):
                        raise Trap(ops.TRAP_TYPE)
                    if len(mp) + (map_key(key) not in mp) > ops.MAX_ALLOC:
                        raise Trap(ops.TRAP_ALLOC)
                    new = dict(mp)
                    new[map_key(key)] = value
                    stack.append(new)
                elif op in _ARITH:
                    b = stack.pop()
                    a = stack.pop()
                    stack.append(_arith(op, a, b))
                elif op == ops.NEG_I64:
                    a = stack.pop()
                    _need_i64(a)
                    if a == I64_MIN:
                        raise Trap(ops.TRAP_OVERFLOW)
                    stack.append(-a)
                elif op == ops.EQ or op == ops.NE:
                    b = stack.pop()
                    a = stack.pop()
                    same = value_eq(a, b)
                    stack.append(same if op == ops.EQ else not same)
                elif op == ops.EQ_STR:
                    b = stack.pop()
                    a = stack.pop()
                    if not isinstance(a, str) or not isinstance(b, str):
                        raise Trap(ops.TRAP_TYPE)
                    stack.append(a == b)
                elif op in _CMP:
                    b = stack.pop()
                    a = stack.pop()
                    _need_i64(a)
                    _need_i64(b)
                    stack.append(_CMP[op](a, b))
                elif op == ops.NOT:
                    v = stack.pop()
                    if not isinstance(v, bool):
                        raise Trap(ops.TRAP_TYPE)
                    stack.append(not v)
                elif op == ops.AND or op == ops.OR:
                    b = stack.pop()
                    a = stack.pop()
                    if not isinstance(a, bool) or not isinstance(b, bool):
                        raise Trap(ops.TRAP_TYPE)
                    stack.append((a and b) if op == ops.AND else (a or b))
                elif op == ops.CALL_FN or op == ops.CALL_TASK:
                    fid, argc = struct.unpack_from("<IH", code, pc)
                    pc += 6
                    callee = self.art.funcs[fid]
                    if op == ops.CALL_FN and callee.effects != 0:
                        raise Trap(ops.TRAP_EFFECT)
                    if op == ops.CALL_TASK and (callee.effects & ~ops.FFI_BIT & ~func.effects):
                        raise Trap(ops.TRAP_EFFECT)
                    call_args = stack[len(stack) - argc :]
                    del stack[len(stack) - argc :]
                    with self.heap.pin(call_args):
                        with self.heap.released():
                            result = self.call(fid, list(call_args))
                        stack.append(result)
                elif op == ops.REQUIRE or op == ops.ENSURE:
                    v = stack.pop()
                    if v is not True:
                        raise Trap(ops.TRAP_REQUIRES if op == ops.REQUIRE else ops.TRAP_ENSURES)
                elif op == ops.PARALLEL:
                    count, mx, timeout = struct.unpack_from("<HHI", code, pc)
                    pc += 8
                    ids = list(struct.unpack_from("<" + "I" * count, code, pc))
                    pc += 4 * count
                    caps = stack[len(stack) - count :]
                    del stack[len(stack) - count :]
                    with self.heap.pin(caps):
                        with self.heap.released():
                            result = self.run_parallel(ids, list(caps), mx, timeout)
                        stack.append(result)
                else:
                    raise gobyte.e100()
        raise gobyte.e100()

    def const_value(self, ix: int) -> object:
        c = self.art.consts[ix]
        if c.tag == gobyte.TAG_UNIT:
            return UNIT
        if c.tag == gobyte.TAG_NONE:
            return NONE
        if c.tag == gobyte.TAG_EFFECT:
            return c.value
        return c.value

    # -- structured concurrency ------------------------------------------

    def run_parallel(self, ids: list[int], caps: list, mx: int, timeout_ms: int) -> list:
        if not isinstance(mx, int) or isinstance(mx, (bool, I32, U32, U64)) or mx < 1:
            raise Trap(ops.TRAP_PAR_MAX)
        if (not isinstance(timeout_ms, int) or
                isinstance(timeout_ms, (bool, I32, U32, U64)) or timeout_ms < 1):
            raise Trap(ops.TRAP_TIMEOUT)
        results: list = [None] * len(ids)
        with self.heap.pin(results):
            return self._parallel(ids, caps, mx, timeout_ms, results)

    def _parallel(self, ids, caps, mx, timeout_ms, results):
        import time
        self.check_cancelled()
        deadline = time.monotonic_ns() + timeout_ms * 1_000_000
        if self.deadline_ns is not None:
            deadline = min(deadline, self.deadline_ns)
        with self.parallel_budget.reserve(min(mx, len(ids))):
            return self._parallel_admitted(ids, caps, mx, deadline, results)

    def _parallel_admitted(self, ids, caps, mx, deadline, results):
        import time as _time
        errors: list = [None] * len(ids)
        stop = threading.Event()
        threads: list[threading.Thread] = []
        pending = iter(range(len(ids)))
        schedule = threading.Lock()

        inherited = self.cancels
        authority = self.authority
        identity = self.request_identity

        def worker() -> None:
            try:
                self.cancels = inherited + (stop,)
                self.deadline_ns = deadline
                self._tl.authority = authority
                self._tl.identity = identity
                while True:
                    with schedule:
                        if any(cancel.is_set() for cancel in self.cancels):
                            return
                        if _time.monotonic_ns() >= deadline:
                            stop.set()
                            return
                        index = next(pending, None)
                        if index is None:
                            return
                    try:
                        result = self.call(ids[index], [caps[index]])
                        with self.heap.lock:
                            results[index] = result
                            self.heap.release_result()
                    except Trap as t:
                        errors[index] = t
                        stop.set()
                    except Cancelled as c:
                        errors[index] = c
                    except Exception as exc:  # native failure
                        errors[index] = exc
                        stop.set()

            finally:
                self.heap.release_result()

        timed_out = False
        try:
            for i in range(min(mx, len(ids))):
                th = threading.Thread(target=worker, daemon=True)
                threads.append(th)
                try:
                    th.start()
                except (RuntimeError, OSError) as error:
                    raise Trap(ops.TRAP_PAR_MAX) from error
            for th in threads:
                while th.is_alive():
                    if any(cancel.is_set() for cancel in inherited):
                        stop.set()
                        break
                    left = deadline - _time.monotonic_ns()
                    if left <= 0:
                        timed_out = True
                        stop.set()
                        break
                    th.join(min(left, 50_000_000) / 1_000_000_000)
                if timed_out or any(cancel.is_set() for cancel in inherited):
                    break
            timed_out = timed_out or _time.monotonic_ns() >= deadline
        finally:
            # Admission is held until every successfully started worker exits,
            # including partial thread-start failure and coordinator exceptions.
            stop.set()
            for th in threads:
                if th.ident is not None:
                    th.join()
        if timed_out:
            raise Trap(ops.TRAP_TIMEOUT)
        if any(cancel.is_set() for cancel in inherited):
            raise Cancelled()
        for e in errors:
            if isinstance(e, Trap):
                raise e
            if isinstance(e, Exception) and not isinstance(e, Cancelled):
                raise e
        return self.heap.handoff(results)


_MISSING = object()
_ARITH = (ops.ADD_I64, ops.SUB_I64, ops.MUL_I64, ops.DIV_I64, ops.MOD_I64)
_CMP = {
    ops.LT_I64: lambda a, b: a < b,
    ops.LE_I64: lambda a, b: a <= b,
    ops.GT_I64: lambda a, b: a > b,
    ops.GE_I64: lambda a, b: a >= b,
}


def _need_i64(v: object) -> None:
    if not isinstance(v, int) or isinstance(v, (bool, I32, U32, U64)):
        raise Trap(ops.TRAP_TYPE)


def _arith(op: int, a: object, b: object) -> int:
    _need_i64(a)
    _need_i64(b)
    if op == ops.ADD_I64:
        out = a + b
    elif op == ops.SUB_I64:
        out = a - b
    elif op == ops.MUL_I64:
        out = a * b
    elif op == ops.DIV_I64:
        if b == 0:
            raise Trap(ops.TRAP_DIV_ZERO)
        out = abs(a) // abs(b)
        out = -out if (a < 0) != (b < 0) else out
    else:
        if b == 0:
            raise Trap(ops.TRAP_DIV_ZERO)
        out = abs(a) % abs(b)
        out = -out if a < 0 else out
    if out < I64_MIN or out > I64_MAX:
        raise Trap(ops.TRAP_OVERFLOW)
    return out


def scalar_type_id(v: object) -> int:
    """VALUE_TYPE for a scalar union member (see ops.SCALAR_TYPE_IDS)."""
    if isinstance(v, bool):
        return ops.SCALAR_TYPE_IDS["bool"]
    for value_type, name in ((I32, "i32"), (U32, "u32"), (U64, "u64")):
        if isinstance(v, value_type):
            return ops.SCALAR_TYPE_IDS[name]
    if isinstance(v, int):
        return ops.SCALAR_TYPE_IDS["i64"]
    if isinstance(v, str):
        return ops.SCALAR_TYPE_IDS["str"]
    if isinstance(v, bytes):
        return ops.SCALAR_TYPE_IDS["bytes"]
    if isinstance(v, Unit):
        return ops.SCALAR_TYPE_IDS["unit"]
    raise Trap(ops.TRAP_TYPE)


def map_key(key: object) -> object:
    if isinstance(key, (bool, int, str)):
        return key
    raise Trap(ops.TRAP_TYPE)


def value_eq(a: object, b: object) -> bool:
    """Structural equality (S20). f64 and opaque values never reach here."""
    if isinstance(a, float) or isinstance(b, float):
        raise Trap(ops.TRAP_TYPE)
    if isinstance(a, (Secret, Buffer, BufferView)) or isinstance(b, (Secret, Buffer, BufferView)):
        raise Trap(ops.TRAP_TYPE)
    # Charged scalar subclasses carry ownership, not distinct language types.
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if isinstance(a, bytes) and isinstance(b, bytes):
        return a == b
    if isinstance(a, int) and isinstance(b, int):
        return scalar_type_id(a) == scalar_type_id(b) and a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(value_eq(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return (len(a) == len(b) and
                all(k in b and value_eq(a[k], b[k]) for k in a))
    # A checked union may have different active member types on either side.
    if type(a) is not type(b):
        return False
    if isinstance(a, Unit) and isinstance(b, Unit):
        return True
    if isinstance(a, NoneValue) or isinstance(b, NoneValue):
        return isinstance(a, NoneValue) and isinstance(b, NoneValue)
    if isinstance(a, Some) and isinstance(b, Some):
        return value_eq(a.value, b.value)
    if isinstance(a, bool) or isinstance(b, bool):
        if not (isinstance(a, bool) and isinstance(b, bool)):
            raise Trap(ops.TRAP_TYPE)
        return a is b
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if isinstance(a, bytes) and isinstance(b, bytes):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(value_eq(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return False
        return all(value_eq(a[k], b[k]) for k in a)
    if isinstance(a, Record) and isinstance(b, Record):
        if a.type_id != b.type_id:
            return False
        return len(a.fields) == len(b.fields) and all(value_eq(x, y) for x, y in zip(a.fields, b.fields))
    if isinstance(a, EnumVal) and isinstance(b, EnumVal):
        if a.type_id != b.type_id or a.variant != b.variant:
            return False
        return len(a.fields) == len(b.fields) and all(value_eq(x, y) for x, y in zip(a.fields, b.fields))
    if type(a) is not type(b):
        raise Trap(ops.TRAP_TYPE)
    return a == b


def require_finite_values(value: object, *, resource_heap=None) -> None:
    """Check host/native value graphs at each admission boundary, cycle-safe."""
    from gopyt.heap import children
    pending = [value]
    seen = set()
    while pending:
        item = pending.pop()
        if isinstance(item, float):
            if not math.isfinite(item):
                raise Trap(ops.TRAP_TYPE)
        elif isinstance(item, (Buffer, BufferView)):
            owner = item.owner if isinstance(item, BufferView) else item
            if resource_heap is not None and getattr(owner, "_vm_heap", None) is not resource_heap:
                raise Trap(ops.TRAP_TYPE)
        elif isinstance(item, (list, dict, Record, EnumVal, Some)):
            identity = id(item)
            if identity not in seen:
                seen.add(identity)
                pending.extend(children(item))
