"""Worker pool: N threads consuming from the queue, with mutex-protected shared stats."""
import threading
import time
import traceback
from collections import Counter

from .queue import BoundedJobQueue, QueuedJob

class JobTimeout(Exception):
    pass

class JobContext:
    """Handed to every handler. Handlers call ctx.check() between steps so a
    job that exceeds its deadline is aborted cooperatively (threads can't be killed)."""
    def __init__(self, job: dict, timeout_s: float, worker: str):
        self.job, self.worker = job, worker
        self.deadline = time.monotonic() + timeout_s
    def check(self):
        if time.monotonic() > self.deadline:
            raise JobTimeout(f"job {self.job['id']} exceeded its time limit")

class PoolStats:
    """Shared mutable state -> every access goes through one lock (mutex)."""
    def __init__(self):
        self._lock = threading.Lock()
        self.running = 0
        self.max_concurrent = 0
        self.completed = 0
        self.failed = 0
        self.retried = 0
        self.by_type = Counter()

    def started(self):
        with self._lock:
            self.running += 1
            self.max_concurrent = max(self.max_concurrent, self.running)

    def finished(self, job_type: str, outcome: str):
        with self._lock:
            self.running -= 1
            if outcome == "done":
                self.completed += 1
                self.by_type[job_type] += 1
            elif outcome == "failed":
                self.failed += 1
            elif outcome == "retry":
                self.retried += 1

    def snapshot(self):
        with self._lock:
            return {"running": self.running, "max_concurrent": self.max_concurrent,
                    "completed": self.completed, "failed": self.failed,
                    "retried": self.retried, "by_type": dict(self.by_type)}

class WorkerPool:
    def __init__(self, cfg, queue: BoundedJobQueue, repo, handlers: dict, stats: PoolStats | None = None):
        self.cfg, self.queue, self.repo, self.handlers = cfg, queue, repo, handlers
        self.stats = stats or PoolStats()
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._timers: set[threading.Timer] = set()
        self._timers_lock = threading.Lock()

    def start(self):
        for i in range(self.cfg.workers):
            t = threading.Thread(target=self._loop, name=f"worker-{i+1}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout=10.0):
        self._stop.set()
        self.queue.close()
        with self._timers_lock:
            for t in self._timers:
                t.cancel()
        for t in self._threads:
            t.join(timeout)
        self._threads.clear()

    # ---------------------------------------------------------------- loop
    def _loop(self):
        name = threading.current_thread().name
        while not self._stop.is_set():
            qj = self.queue.get(timeout=0.5)
            if qj is None:
                continue
            try:
                job = self.repo.claim_job(qj.job_id, name)   # atomic: only one worker wins
            except Exception:
                traceback.print_exc()
                continue
            if job is None:          # cancelled, or already taken -> skip
                continue
            self._run(job, name)

    def _run(self, job: dict, name: str):
        self.stats.started()
        outcome = "failed"
        try:
            handler = self.handlers[job["job_type"]]
            ctx = JobContext(job, self.cfg.job_timeout_s, name)
            result = handler(job, ctx)
            self.repo.finish_job(job["id"], result)
            outcome = "done"
        except Exception as e:                                # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"
            row = self.repo.fail_job(job["id"], msg)
            if row and row["status"] == "READY":              # DB decided: retry
                outcome = "retry"
                self._requeue_later(row)
            else:
                outcome = "failed"
        finally:
            self.stats.finished(job["job_type"], outcome)

    def _requeue_later(self, row: dict):
        delay = self.cfg.retry_backoff_s * (2 ** (row["attempts"] - 1))   # exponential backoff
        qj = QueuedJob(row["id"], row["priority"], row["job_type"])
        def fire():
            try:
                self.queue.put(qj, force=True)
            except Exception:
                pass          # queue closed on shutdown; recover_jobs() picks it up next start
        t = threading.Timer(delay, fire)
        t.daemon = True
        with self._timers_lock:
            self._timers.add(t)
        t.start()
