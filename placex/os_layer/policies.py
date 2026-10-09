"""Pluggable scheduling policies. A policy answers one question:
   'given the waiting jobs, which index should run next?'"""
from abc import ABC, abstractmethod

class Policy(ABC):
    name = "base"
    @abstractmethod
    def select(self, items: list, now: float) -> int: ...

class FCFS(Policy):
    """First come, first served. Fair but a slow job blocks everyone behind it."""
    name = "fcfs"
    def select(self, items, now):
        return min(range(len(items)), key=lambda i: items[i].seq)

class StrictPriority(Policy):
    """Highest priority first. Fast for urgent work, but low priority can starve."""
    name = "priority"
    def select(self, items, now):
        return max(range(len(items)), key=lambda i: (items[i].priority, -items[i].seq))

class PriorityAging(Policy):
    """Priority + aging: every `aging_seconds` of waiting adds +1 effective priority,
    so even the lowest-priority job eventually wins. Prevents starvation."""
    name = "priority_aging"
    def __init__(self, aging_seconds: float = 10.0):
        self.aging_seconds = max(aging_seconds, 1e-6)
    def score(self, item, now):
        return item.priority + (now - item.enqueued_at) / self.aging_seconds
    def select(self, items, now):
        return max(range(len(items)), key=lambda i: (self.score(items[i], now), -items[i].seq))

class ShortestJobFirst(Policy):
    """Shortest (estimated) job first: minimises average waiting time."""
    name = "sjf"
    COST = {"JOURNEY_GUIDE": 1, "PREP_PLAN": 2, "RESUME_BUILD": 3, "RESUME_ANALYSIS": 4}
    def select(self, items, now):
        return min(range(len(items)), key=lambda i: (self.COST.get(items[i].job_type, 5), items[i].seq))

def make_policy(name: str, aging_seconds: float = 10.0) -> Policy:
    table = {"fcfs": FCFS, "priority": StrictPriority, "sjf": ShortestJobFirst}
    if name == "priority_aging":
        return PriorityAging(aging_seconds)
    if name in table:
        return table[name]()
    raise ValueError(f"unknown policy {name!r}")
