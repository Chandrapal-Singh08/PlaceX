"""Connection pool + transaction helper. Every unit of work = one transaction."""
from contextlib import contextmanager
import logging
import socket
import threading

from psycopg import InterfaceError, OperationalError, ProgrammingError
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()
logger = logging.getLogger(__name__)


class DatabaseUnavailable(RuntimeError):
    """The configured database could not be reached for this operation."""

    def __init__(self, message: str, *, category: str = "database_unavailable"):
        super().__init__(message)
        self.category = category


def _failure_category(error: Exception) -> str:
    sqlstate = getattr(error, "sqlstate", None)
    if sqlstate in {"28P01", "28000"}:
        return "authentication"
    message = str(error).casefold()
    if any(term in message for term in ("resolve host", "name or service", "nodename", "gaierror")):
        return "dns"
    if "ssl" in message or "tls" in message or "certificate" in message:
        return "ssl"
    if any(term in message for term in ("timeout", "timed out", "refused", "network", "unreachable")):
        return "network"
    return "database_unavailable"


def init_pool(
    dsn: str,
    min_size: int = 2,
    max_size: int = 20,
    wait_timeout: float = 2.0,
    connect_timeout: int = 5,
    reconnect_timeout: float = 30.0,
) -> ConnectionPool:
    global _pool
    if min_size < 0 or max_size < 1 or min_size > max_size:
        raise ValueError("database pool sizes must satisfy 0 <= min_size <= max_size")
    if wait_timeout <= 0 or connect_timeout <= 0 or reconnect_timeout <= 0:
        raise ValueError("database connection and pool timeouts must be positive")

    conninfo = conninfo_to_dict(dsn)
    host = conninfo.get("host", "")
    if host.endswith(".supabase.co") and not conninfo.get("sslmode"):
        dsn = make_conninfo(dsn, sslmode="require")

    with _pool_lock:
        if _pool is not None:
            _pool.close()
        pool = ConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            kwargs={
                "row_factory": dict_row,
                "connect_timeout": connect_timeout,
            },
            open=True,
            timeout=wait_timeout,
            reconnect_timeout=reconnect_timeout,
        )
        _pool = pool
    try:
        pool.wait(timeout=wait_timeout)
    except PoolTimeout:
        logger.exception(
            "database_startup_connection_timeout; backend will start without database access"
        )
    return pool


def close_pool():
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


def check_connection() -> None:
    with transaction() as conn:
        conn.execute("SELECT 1").fetchone()


def diagnose_connection(dsn: str) -> dict:
    """Return safe connection diagnostics without exposing DSN or driver details."""
    try:
        options = conninfo_to_dict(dsn)
    except ProgrammingError:
        return {
            "configuration": "invalid",
            "hostname_resolution": "not_checked",
            "connection": "not_checked",
            "ssl": "not_checked",
            "query": "not_checked",
            "failure_category": "configuration",
        }

    host = options.get("host")
    if not host or not options.get("dbname") or not options.get("user") or not options.get("password"):
        return {
            "configuration": "incomplete",
            "hostname_resolution": "not_checked",
            "connection": "not_checked",
            "ssl": "not_checked",
            "query": "not_checked",
            "failure_category": "configuration",
        }

    try:
        addresses = socket.getaddrinfo(
            host,
            options.get("port") or 5432,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        return {
            "configuration": "loaded",
            "hostname_resolution": "failed",
            "connection": "unavailable",
            "ssl": "not_checked",
            "query": "not_checked",
            "failure_category": "dns",
        }
    except OSError:
        return {
            "configuration": "loaded",
            "hostname_resolution": "failed",
            "connection": "unavailable",
            "ssl": "not_checked",
            "query": "not_checked",
            "failure_category": "network",
        }

    if not addresses:
        return {
            "configuration": "loaded",
            "hostname_resolution": "failed",
            "connection": "unavailable",
            "ssl": "not_checked",
            "query": "not_checked",
            "failure_category": "dns",
        }

    try:
        with transaction() as conn:
            conn.execute("SELECT 1").fetchone()
            ssl_active = conn.info.ssl_in_use
    except DatabaseUnavailable as error:
        cause = error.__cause__ or error
        return {
            "configuration": "loaded",
            "hostname_resolution": "resolved",
            "connection": "unavailable",
            "ssl": "not_checked",
            "query": "unavailable",
            "failure_category": (
                error.category if cause is error else _failure_category(cause)
            ),
        }

    return {
        "configuration": "loaded",
        "hostname_resolution": "resolved",
        "connection": "available",
        "ssl": "enabled" if ssl_active else "disabled",
        "query": "ok",
        "failure_category": None,
    }


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
    with _pool_lock:
        pool = _pool
    if pool is None:
        raise RuntimeError("db.init_pool() was not called")
    try:
        with pool.connection() as conn:
            yield conn
    except (InterfaceError, OperationalError) as error:
        logger.error(
            "database_connection_failed category=%s",
            _failure_category(error),
        )
        raise DatabaseUnavailable(
            "database connection is unavailable",
            category=_failure_category(error),
        ) from error
