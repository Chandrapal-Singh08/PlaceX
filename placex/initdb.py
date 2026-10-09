"""Create / reset the PlaceX database:  python -m placex.initdb [--reset] [--no-seed]"""
import argparse, pathlib
import psycopg
from .config import Config

SQL_DIR = pathlib.Path(__file__).resolve().parent.parent / "sql"

def apply_schema(dsn: str, reset: bool = False, seed: bool = True) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        if reset:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
        conn.execute((SQL_DIR / "schema.sql").read_text())
        if seed:
            conn.execute((SQL_DIR / "seed.sql").read_text())

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="drop everything first")
    ap.add_argument("--no-seed", action="store_true")
    a = ap.parse_args()
    apply_schema(Config().dsn, reset=a.reset, seed=not a.no_seed)
    print("PlaceX schema ready.")
