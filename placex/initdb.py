"""Initialize PlaceX; reset only explicitly disposable databases."""
import argparse, pathlib
import psycopg
from psycopg.conninfo import conninfo_to_dict
from .config import Config

SQL_DIR = pathlib.Path(__file__).resolve().parent.parent / "sql"
RESETTABLE_DATABASE_SUFFIXES = ("_test", "_demo", "_bench", "_benchmark")


def _check_reset_target(dsn: str) -> None:
    database = conninfo_to_dict(dsn).get("dbname", "")
    if not database.endswith(RESETTABLE_DATABASE_SUFFIXES):
        raise ValueError(
            "Refusing to reset this database. Use a disposable database ending "
            "in _test, _demo, _bench, or _benchmark."
        )


def apply_schema(dsn: str, reset: bool = False, seed: bool = True) -> None:
    if reset:
        _check_reset_target(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn:
        if reset:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
        conn.execute((SQL_DIR / "schema.sql").read_text())
        if seed:
            conn.execute((SQL_DIR / "seed.sql").read_text())

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--reset",
        action="store_true",
        help="drop the schema first (database name must end in _test, _demo, _bench, or _benchmark)",
    )
    ap.add_argument("--no-seed", action="store_true")
    a = ap.parse_args()
    apply_schema(Config().dsn, reset=a.reset, seed=not a.no_seed)
    print("PlaceX schema ready.")
