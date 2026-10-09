"""Resource monitor: samples CPU / RAM / queue depth and applies admission control.
When memory or CPU is too high the queue is paused (workers stop taking new jobs);
it resumes only after usage drops below the LOW mark (hysteresis -> no flapping)."""
import threading
import time
import logging
import re
import subprocess
import sys
from collections import deque

import psutil

_MAC_FREE_MEMORY = re.compile(r"System-wide memory free percentage:\s*(\d+(?:\.\d+)?)%")
logger = logging.getLogger(__name__)


def _memory_percent():
    if sys.platform != "darwin":
        return psutil.virtual_memory().percent

    result = subprocess.run(
        ["memory_pressure", "-Q"],
        capture_output=True,
        check=True,
        text=True,
        timeout=2,
    )
    match = _MAC_FREE_MEMORY.search(result.stdout)
    if not match:
        raise RuntimeError("memory_pressure did not report system-wide free memory")
    return 100.0 - float(match.group(1))


def default_probe():
    proc = psutil.Process()
    return (psutil.cpu_percent(interval=None),
            _memory_percent(),
            proc.memory_info().rss / 1_048_576)

class ResourceMonitor(threading.Thread):
    def __init__(self, cfg, queue, stats=None, on_event=None, probe=default_probe):
        super().__init__(name="monitor", daemon=True)
        self.cfg, self.queue, self.stats = cfg, queue, stats
        self.on_event = on_event or (lambda action, details: None)
        self.probe = probe
        self.samples = deque(maxlen=300)
        self.throttled = False
        self._stop_evt = threading.Event()

    def sample_once(self) -> dict:
        cpu, mem, rss = self.probe()
        s = {"t": time.time(), "cpu": cpu, "mem": mem, "rss_mb": round(rss, 1),
             "queue_depth": len(self.queue),
             "running": self.stats.snapshot()["running"] if self.stats else None}
        self.samples.append(s)
        c = self.cfg
        if not self.throttled and (mem >= c.mem_high_pct or cpu >= c.cpu_high_pct):
            self.throttled = True
            self.queue.pause()
            self.on_event("admission_paused", s)
        elif self.throttled and mem <= c.mem_low_pct and cpu <= c.cpu_low_pct:
            self.throttled = False
            self.queue.resume()
            self.on_event("admission_resumed", s)
        return s

    def run(self):
        while not self._stop_evt.wait(self.cfg.monitor_interval_s):
            try:
                self.sample_once()
            except Exception:
                logger.exception("Resource monitor sampling failed")

    def stop(self):
        self._stop_evt.set()
        if self.throttled:
            self.queue.resume()

    def latest(self):
        return self.samples[-1] if self.samples else None
