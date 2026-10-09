"""Memory management: a thread-safe LRU cache with TTL, plus a load-once model registry."""
import threading
import time
from collections import OrderedDict

class LRUCache:
    """Bounded cache. Evicts the least-recently-used entry when full; entries expire after ttl."""
    def __init__(self, max_items=256, ttl=None):
        self.max_items, self.ttl = max_items, ttl
        self._d: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        self.hits = self.misses = 0

    def get(self, key, default=None):
        with self._lock:
            entry = self._d.get(key)
            if entry is None or (self.ttl and time.monotonic() - entry[1] > self.ttl):
                if entry is not None:
                    del self._d[key]
                self.misses += 1
                return default
            self._d.move_to_end(key)
            self.hits += 1
            return entry[0]

    def put(self, key, value):
        with self._lock:
            self._d[key] = (value, time.monotonic())
            self._d.move_to_end(key)
            while len(self._d) > self.max_items:
                self._d.popitem(last=False)

    def get_or_compute(self, key, fn):
        sentinel = object()
        v = self.get(key, sentinel)
        if v is sentinel:
            v = fn()
            self.put(key, v)
        return v

    def stats(self):
        with self._lock:
            total = self.hits + self.misses
            return {"size": len(self._d), "hits": self.hits, "misses": self.misses,
                    "hit_rate": round(self.hits / total, 3) if total else 0.0}

class ModelRegistry:
    """Heavy objects (spaCy / Sentence-Transformer models) are loaded ONCE and shared by
    all workers. Double-checked locking keeps 16 threads from loading 16 copies into RAM."""
    def __init__(self):
        self._models, self._lock = {}, threading.Lock()

    def get(self, name, loader):
        m = self._models.get(name)
        if m is None:
            with self._lock:
                m = self._models.get(name)
                if m is None:
                    m = self._models[name] = loader()
        return m
