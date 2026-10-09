"""Bounded producer-consumer queue built from a mutex + two condition variables.

Producers (API threads)  --put()-->  [ bounded buffer ]  --get()-->  Consumers (workers)

* `not_full`  : producers wait here when the buffer is full (backpressure)
* `not_empty` : consumers wait here when empty OR when admission control paused us
* the scheduling policy decides WHICH item get() hands out
"""
import itertools
import threading
import time
from dataclasses import dataclass, field

class QueueFull(Exception):
    pass

@dataclass
class QueuedJob:
    job_id: int
    priority: int = 5
    job_type: str = ""
    enqueued_at: float = field(default_factory=time.monotonic)
    seq: int = 0

class BoundedJobQueue:
    def __init__(self, capacity: int, policy):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self.policy = policy
        self._items: list[QueuedJob] = []
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._not_full = threading.Condition(self._lock)
        self._paused = False
        self._closed = False
        self._seq = itertools.count(1)

    # ------------------------------------------------------------ producer
    def put(self, item: QueuedJob, block=False, timeout=None, force=False):
        """force=True bypasses the capacity limit (used for retries / crash recovery,
        which must never be dropped)."""
        with self._not_full:
            if self._closed:
                raise QueueFull("queue closed")
            if not force and len(self._items) >= self.capacity:
                if not block:
                    raise QueueFull("queue full")
                deadline = None if timeout is None else time.monotonic() + timeout
                while len(self._items) >= self.capacity and not self._closed:
                    remaining = None if deadline is None else deadline - time.monotonic()
                    if remaining is not None and remaining <= 0:
                        raise QueueFull("timed out waiting for space")
                    self._not_full.wait(remaining)
                if self._closed:
                    raise QueueFull("queue closed")
            item.seq = next(self._seq)
            self._items.append(item)
            self._not_empty.notify()

    # ------------------------------------------------------------ consumer
    def get(self, timeout=None):
        """Returns the next job per policy, or None on timeout / shutdown."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._not_empty:
            while (not self._items or self._paused) and not self._closed:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return None
                self._not_empty.wait(remaining)
            if self._closed:
                return None
            idx = self.policy.select(self._items, time.monotonic())
            item = self._items.pop(idx)
            self._not_full.notify()
            return item

    # ------------------------------------------------- admission control
    def pause(self):
        with self._lock:
            self._paused = True

    def resume(self):
        with self._not_empty:
            self._paused = False
            self._not_empty.notify_all()

    def close(self):
        with self._lock:
            self._closed = True
            self._not_empty.notify_all()
            self._not_full.notify_all()

    @property
    def paused(self):
        with self._lock:
            return self._paused

    def full(self):
        with self._lock:
            return len(self._items) >= self.capacity

    def __len__(self):
        with self._lock:
            return len(self._items)

    def snapshot(self):
        with self._lock:
            return {"depth": len(self._items), "capacity": self.capacity,
                    "paused": self._paused, "policy": self.policy.name}
