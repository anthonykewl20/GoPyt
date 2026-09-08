"""VM-owned nonmoving tracing heap. Host references are not language roots.

The registry retains every admitted cell until an explicit mark/sweep. The
mutator lock protects instruction boundaries; blocking native calls and joined
parallel work release it while their arguments/captures remain pinned.
"""
from contextlib import contextmanager
import threading
from gopyt.values import Record, EnumVal, Some, Secret

HEAP_TYPES = (str, bytes, list, dict, Record, EnumVal, Some, Secret)


def children(value):
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

    @contextmanager
    def step(self, frame):
        with self.lock:
            for value in frame.stack:
                self._adopt_locked(value)
            for value in frame.locals:
                self._adopt_locked(value)
            # Any returned value is now in its caller's stack/locals.
            self.handoffs.pop(threading.get_ident(), None)
            if len(self.objects) >= self.threshold:
                self.collect()
                self.threshold = max(1024, len(self.objects) * 2)
            yield

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
                obj = self.objects.pop(key)
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
