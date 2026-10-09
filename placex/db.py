"""Connection pool + transaction helper. Every unit of work = one transaction."""
from contextlib import contextmanager
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None

def init_pool(dsn: str, min_size: int = 2, max_size: int = 20) -> ConnectionPool:
    global _pool
    if _pool is not None:
        _pool.close()
    _pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size,
                           kwargs={"row_factory": dict_row}, open=True)
    _pool.wait()
    return _pool

def close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def ensure_auth_schema():
    """Apply additive auth DDL for databases created before password sessions existed."""
    with transaction() as conn:
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS user_sessions (
                   id BIGSERIAL PRIMARY KEY,
                   user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                   token_hash CHAR(64) NOT NULL UNIQUE,
                   expires_at TIMESTAMPTZ NOT NULL,
                   revoked_at TIMESTAMPTZ,
                   created_at TIMESTAMPTZ NOT NULL DEFAULT now()
               )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_sessions_user ON user_sessions (user_id, expires_at DESC)")

@contextmanager
def transaction():
    """COMMIT on success, ROLLBACK on any exception."""
    if _pool is None:
        raise RuntimeError("db.init_pool() was not called")
    with _pool.connection() as conn:
        yield conn
