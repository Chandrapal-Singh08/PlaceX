from dotenv import load_dotenv

load_dotenv()
import os
import json
from dataclasses import dataclass, field

def _e(name, default, cast=str):
    v = os.environ.get(name)
    return cast(v) if v is not None else default

def _match_weights():
    from .matching import MATCH_WEIGHTS, validate_match_weights

    raw = os.environ.get("PLACEX_MATCH_WEIGHTS")
    if raw is None:
        return dict(MATCH_WEIGHTS)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("PLACEX_MATCH_WEIGHTS must be a JSON object") from error
    return validate_match_weights(parsed)

def _bool_env(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")

@dataclass
class Config:
    allowed_origins: str = field(default_factory=lambda: _e("PLACEX_ALLOWED_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500"))
    # --- optional LLM enhancements
    llm_provider: str = field(default_factory=lambda: _e("PLACEX_LLM_PROVIDER", "none").lower())
    groq_api_key: str | None = field(default_factory=lambda: os.environ.get("GROQ_API_KEY"))
    groq_model: str = field(default_factory=lambda: _e("GROQ_MODEL", "openai/gpt-oss-20b"))
    llm_timeout_s: float = field(default_factory=lambda: _e("PLACEX_LLM_TIMEOUT_S", 20.0, float))
    llm_max_retries: int = field(default_factory=lambda: _e("PLACEX_LLM_MAX_RETRIES", 1, int))
    llm_max_output_tokens: int = field(default_factory=lambda: _e("PLACEX_LLM_MAX_OUTPUT_TOKENS", 1200, int))
    journey_llm_max_output_tokens: int = field(default_factory=lambda: _e("PLACEX_JOURNEY_MAX_OUTPUT_TOKENS", 4096, int))
    llm_max_input_chars: int = field(default_factory=lambda: _e("PLACEX_LLM_MAX_INPUT_CHARS", 20000, int))
    matching_weights: dict = field(default_factory=_match_weights)
    embeddings_enabled: bool = field(default_factory=lambda: _bool_env("PLACEX_EMBEDDINGS_ENABLED"))
    embedding_model: str = field(default_factory=lambda: _e(
        "PLACEX_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
    ))
    embedding_device: str | None = field(default_factory=lambda: os.environ.get("PLACEX_EMBEDDING_DEVICE"))
    embedding_local_files_only: bool = field(default_factory=lambda: _bool_env(
        "PLACEX_EMBEDDING_LOCAL_FILES_ONLY", True
    ))
    embedding_similarity_threshold: float = field(default_factory=lambda: _e(
        "PLACEX_EMBEDDING_SIMILARITY_THRESHOLD", 0.72, float
    ))
    job_provider_url: str | None = field(default_factory=lambda: os.environ.get("PLACEX_JOB_PROVIDER_URL"))
    job_provider_name: str = field(default_factory=lambda: _e("PLACEX_JOB_PROVIDER_NAME", "configured-json-api"))
    job_provider_api_key: str | None = field(default_factory=lambda: os.environ.get("PLACEX_JOB_PROVIDER_API_KEY"))
    job_provider_timeout_s: float = field(default_factory=lambda: _e("PLACEX_JOB_PROVIDER_TIMEOUT_S", 10.0, float))
    research_provider: str = field(
        default_factory=lambda: _e("PLACEX_RESEARCH_PROVIDER", "none").strip().lower()
    )
    brave_search_api_key: str | None = field(
        default_factory=lambda: os.environ.get("BRAVE_SEARCH_API_KEY")
    )
    research_timeout_s: float = field(
        default_factory=lambda: _e("PLACEX_RESEARCH_TIMEOUT_S", 10.0, float)
    )
    research_cache_ttl_seconds: int = field(
        default_factory=lambda: _e("PLACEX_RESEARCH_CACHE_TTL_SECONDS", 86400, int)
    )
    research_result_limit: int = field(
        default_factory=lambda: _e("PLACEX_RESEARCH_RESULT_LIMIT", 8, int)
    )
    dsn: str = field(default_factory=lambda: _e("PLACEX_DSN", "postgresql://placex:placex@localhost:5432/placex"))
    db_pool_min_size: int = field(default_factory=lambda: _e("PLACEX_DB_POOL_MIN_SIZE", 2, int))
    db_pool_max_size: int = field(default_factory=lambda: _e("PLACEX_DB_POOL_MAX_SIZE", 16, int))
    db_connect_timeout_s: int = field(default_factory=lambda: _e("PLACEX_DB_CONNECT_TIMEOUT_S", 5, int))
    db_pool_wait_timeout_s: float = field(default_factory=lambda: _e("PLACEX_DB_POOL_WAIT_TIMEOUT_S", 2.0, float))
    db_reconnect_timeout_s: float = field(default_factory=lambda: _e("PLACEX_DB_RECONNECT_TIMEOUT_S", 30.0, float))
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
