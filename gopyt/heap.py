"""VM-owned nonmoving tracing heap. Host references are not language roots.

The registry retains every admitted cell until an explicit mark/sweep. The
mutator lock protects instruction boundaries; blocking native calls and joined
parallel work release it while their arguments/captures remain pinned.
"""
from contextlib import contextmanager
from itertools import islice
import threading
from gopyt.values import Record, EnumVal, Some, Secret
from gopyt.resource_buffer import Buffer, BufferView

HEAP_TYPES = (str, bytes, list, dict, Record, EnumVal, Some, Secret, Buffer, BufferView)
SCALAR_TYPES = (int, bool, float, type(None))


class _InstructionGuard:
    """Reusable frame guard with bounded batches and explicit final release."""
    __slots__ = ('heap', 'frame', 'quantum', 'remaining', 'held')

    def __init__(self, heap, frame, quantum=1):
        self.heap = heap
        self.frame = frame
        self.quantum = quantum
        self.remaining = 0
        self.held = False

    def __enter__(self):
        heap = self.heap
        if not self.held:
            heap.lock.acquire()
            self.held = True
            self.remaining = self.quantum
        try:
            for value in self.frame.stack:
                heap._adopt_locked(value)
            for value in self.frame.locals:
                heap._adopt_locked(value)
            heap.handoffs.pop(threading.get_ident(), None)
            if len(heap.objects) >= heap.threshold:
                heap.collect()
                heap.threshold = max(1024, len(heap.objects) * 2)
        except BaseException:
            self.finish()
            raise

    def __exit__(self, exc_type, exc, traceback):
        self.remaining -= 1
        if exc_type is not None or self.remaining == 0:
            self.finish()
        return False

    def finish(self):
        if self.held:
            self.held = False
            self.heap.lock.release()
            self.heap.drain_resources()


def children(value):
    if isinstance(value, BufferView):
        return (value.owner,)
    if isinstance(value, (Record, EnumVal)):
        return value.fields
    if isinstance(value, Some):
        return (value.value,)
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return (*value.keys(), *value.values())
    # Secret's foreign credential text is opaque, never inspected by tracing.
    return ()


class Heap:
    def __init__(self, constants=(), threshold=1024):
        self.lock = threading.RLock()
        self.objects = {}
        self.frames = {}
        self.pins = {}
        self.handoffs = {}
        self.constants = tuple(constants)
        self.threshold = threshold
        self._resource_lock = threading.Lock()
        self._resource_pending = {}
        self._resource_draining = False
        self.collections = 0
        self.reclaimed = 0
        self.adopt(self.constants)

    def adopt(self, value):
        with self.lock:
            self._adopt_locked(value)
        return value

    def _adopt_locked(self, value):
        """Admit a value while the caller holds the mutator lock.

        Most instruction roots are scalars or already registered. Avoid making
        a traversal list and reacquiring the RLock for each such root. Newly
        admitted cells retain exactly the same recursive tracing behavior.
        """
        if type(value) in SCALAR_TYPES:
            return
        if isinstance(value, tuple):
            pending = list(value)
        elif not isinstance(value, HEAP_TYPES) or id(value) in self.objects:
            return
        else:
            pending = [value]
        while pending:
            obj = pending.pop()
            if not isinstance(obj, HEAP_TYPES) or id(obj) in self.objects:
                continue
            self.objects[id(obj)] = obj
            pending.extend(children(obj))

    def roots(self):
        yield from self.constants
        yield from self.pins.values()
        yield from self.handoffs.values()
        for frame in self.frames.values():
            yield from frame.stack
            yield from frame.locals

    @contextmanager
    def pin(self, value):
        token = object()
        with self.lock:
            self.pins[token] = value
            self.adopt(value)
        try:
            yield value
        finally:
            with self.lock:
                del self.pins[token]

    @contextmanager
    def frame(self, frame):
        with self.lock:
            self.frames[id(frame)] = frame
        try:
            yield
        finally:
            with self.lock:
                del self.frames[id(frame)]

    def step(self, frame, *, quantum=1):
        if type(quantum) is not int or not 1 <= quantum <= 32:
            raise ValueError('instruction quantum must be between 1 and 32')
        return _InstructionGuard(self, frame, quantum)

    @contextmanager
    def released(self):
        # Exactly one instruction lock is held here. Calls do not retain their
        # parent's instruction lock; recursive calls therefore stay balanced.
        self.lock.release()
        try:
            yield
        finally:
            self.lock.acquire()

    def handoff(self, value):
        with self.lock:
            self.adopt(value)
            self.handoffs[threading.get_ident()] = value
            if len(self.objects) >= self.threshold:
                self.collect()
                self.threshold = max(1024, len(self.objects) * 2)
        return value

    def release_result(self):
        with self.lock:
            self.handoffs.pop(threading.get_ident(), None)

    def collect(self):
        with self.lock:
            marked = set()
            pending = list(self.roots())
            while pending:
                obj = pending.pop()
                if not isinstance(obj, HEAP_TYPES) or id(obj) in marked:
                    continue
                marked.add(id(obj))
                self.objects[id(obj)] = obj
                pending.extend(children(obj))
            dead = [key for key in self.objects if key not in marked]
            for key in dead:
                obj = self.objects[key]
                if isinstance(obj, (Buffer, BufferView)):
                    with self._resource_lock:
                        self._resource_pending[id(obj)] = obj
                del self.objects[key]
                # Break language cycles without delegating them to Python GC.
                if isinstance(obj, (list, dict)):
                    obj.clear()
                elif isinstance(obj, (Record, EnumVal)):
                    obj.fields = []
                elif isinstance(obj, Some):
                    obj.value = None
                elif isinstance(obj, Secret):
                    obj.text = ''
            self.collections += 1
            self.reclaimed += len(dead)
            return len(dead)

    def defer_resource(self, resource):
        """Retain an unpublished resource before attempting fallible cleanup."""
        if not isinstance(resource, (Buffer, BufferView)):
            raise TypeError('buffer resource required')
        with self._resource_lock:
            self._resource_pending[id(resource)] = resource

    def drain_resources(self, limit=64):
        """Drain deferred closes outside the mutator lock; retain unfinished work.

        Instruction guards invoke this after releasing their sole mutator lock.
        Embedders that call collect directly must drain after leaving heap.lock.
        No external resource closer runs during marking or under either queue lock.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError('positive resource drain limit required')
        if not self._resource_pending:
            return 0
        with self._resource_lock:
            if self._resource_draining:
                return 0
            selected = list(islice(self._resource_pending.items(), limit))
            # Preparing the batch may allocate. Publish the active-drain flag
            # only after preparation succeeds, so MemoryError leaves retries live.
            self._resource_draining = True
        completed = 0
        try:
            for key, resource in selected:
                try:
                    released = resource.close()
                except BaseException:
                    # Keep the object and its charge for a later explicit retry.
                    released = False
                if released:
                    with self._resource_lock:
                        self._resource_pending.pop(key, None)
                    completed += 1
        finally:
            with self._resource_lock:
                self._resource_draining = False
        return completed

    def pending_resources(self):
        with self._resource_lock:
            return len(self._resource_pending)

    def close_resources(self):
        """Queue even pinned resources at VM teardown; call outside heap.lock."""
        with self.lock:
            with self._resource_lock:
                for key, value in self.objects.items():
                    if isinstance(value, (Buffer, BufferView)):
                        self._resource_pending[key] = value
        # One pass over current work; failed closers remain owned for host retry.
        pending = self.pending_resources()
        if pending:
            self.drain_resources(limit=pending)
