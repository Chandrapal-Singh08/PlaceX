import os
from dataclasses import dataclass, field

def _e(name, default, cast=str):
    v = os.environ.get(name)
    return cast(v) if v is not None else default

@dataclass
class Config:
    dsn: str = field(default_factory=lambda: _e("PLACEX_DSN", "postgresql://placex:placex@localhost:5432/placex"))
    # --- OS layer
    workers: int = field(default_factory=lambda: _e("PLACEX_WORKERS", 4, int))
    queue_capacity: int = field(default_factory=lambda: _e("PLACEX_QUEUE_CAP", 100, int))
    policy: str = field(default_factory=lambda: _e("PLACEX_POLICY", "priority_aging"))  # fcfs|priority|priority_aging|sjf
    aging_seconds: float = 10.0          # +1 priority point per this many seconds of waiting
    job_timeout_s: float = 60.0
    retry_backoff_s: float = 1.0
    # --- resource monitor (admission control with hysteresis)
    monitor_interval_s: float = 1.0
    mem_high_pct: float = field(default_factory=lambda: _e("PLACEX_MEM_HIGH_PCT", 85.0, float))
    mem_low_pct: float = field(default_factory=lambda: _e("PLACEX_MEM_LOW_PCT", 75.0, float))
    cpu_high_pct: float = field(default_factory=lambda: _e("PLACEX_CPU_HIGH_PCT", 95.0, float))
    cpu_low_pct: float = field(default_factory=lambda: _e("PLACEX_CPU_LOW_PCT", 80.0, float))
    # --- file system
    upload_dir: str = field(default_factory=lambda: _e("PLACEX_UPLOAD_DIR", "./uploads"))
    max_upload_mb: float = 5.0
    user_quota_mb: float = 50.0
    generated_ttl_days: int = 30
