import os, subprocess, sys, tempfile
import psycopg, pytest

ADMIN = os.environ.get("PLACEX_ADMIN_DSN", "postgresql://placex:placex@localhost:5432/postgres")
TEST_DSN = os.environ.get("PLACEX_TEST_DSN", "postgresql://placex:placex@localhost:5432/placex_test")

@pytest.fixture(scope="session", autouse=True)
def _database():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS placex_test WITH (FORCE)")
        c.execute("CREATE DATABASE placex_test")
    from placex.initdb import apply_schema
    apply_schema(TEST_DSN, reset=True, seed=True)
    yield

@pytest.fixture(autouse=True)
def clean(_database):
    """Wipe mutable tables before each test; seed data (skills, JDs, rounds) stays."""
    with psycopg.connect(TEST_DSN, autocommit=True) as c:
        c.execute("TRUNCATE users, jobs, activity_logs RESTART IDENTITY CASCADE")

@pytest.fixture
def conn():
    with psycopg.connect(TEST_DSN, autocommit=True, row_factory=psycopg.rows.dict_row) as c:
        yield c

@pytest.fixture
def cfg(tmp_path):
    from placex.config import Config
    c = Config(dsn=TEST_DSN, workers=4, queue_capacity=50, upload_dir=str(tmp_path / "up"))
    c.monitor_interval_s = 0.05
    c.retry_backoff_s = 0.05
    return c

@pytest.fixture
def core(cfg):
    from placex.core import PlaceXCore
    k = PlaceXCore(cfg, probe=lambda: (1.0, 10.0, 50.0)).start()
    yield k
    k.stop()

@pytest.fixture
def user(core):
    return core.repo.create_user("stu@geu.ac.in", "Test Student", "240000001")
