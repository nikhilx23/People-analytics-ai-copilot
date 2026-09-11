"""
Loads data/synthetic_employee_data.csv into the configured database
(SQLite by default, or PostgreSQL if DATABASE_URL is set), applying the
dialect-appropriate schema first (drop + recreate, so this is safe to re-run).

Run:
    python3 -m app.database.load_data
"""

import csv
import re
from pathlib import Path

from sqlalchemy import text

from app.database.connection import get_engine, is_postgres

ROOT = Path(__file__).resolve().parent.parent.parent
SQLITE_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
POSTGRES_SCHEMA_PATH = Path(__file__).resolve().parent / "schema_postgres.sql"
CSV_PATH = ROOT / "data" / "synthetic_employee_data.csv"

COLUMNS = [
    "employee_id", "full_name", "department", "job_title", "job_level",
    "location", "hire_date", "termination_date", "termination_type",
    "termination_reason", "employee_status", "salary", "performance_rating",
    "manager", "last_promotion_date",
]

NULLABLE_STRING_COLS = ("termination_date", "termination_type", "termination_reason",
                         "manager", "last_promotion_date")


def _split_statements(script: str):
    # Our schema files are simple DDL with no semicolons inside string
    # literals, so a plain split is safe once line comments are stripped.
    # Strip per-line (not "does the whole statement start with --") so a
    # comment line followed by real SQL in the same statement isn't
    # dropped entirely.
    no_comments = "\n".join(re.sub(r"--.*", "", line) for line in script.splitlines())
    for stmt in no_comments.split(";"):
        stmt = stmt.strip()
        if stmt:
            yield stmt


def main():
    if not CSV_PATH.exists():
        raise SystemExit(f"Missing {CSV_PATH}. Run data/generate_synthetic_data.py first.")

    engine = get_engine()
    schema_path = POSTGRES_SCHEMA_PATH if is_postgres() else SQLITE_SCHEMA_PATH

    with engine.begin() as conn:
        for stmt in _split_statements(schema_path.read_text()):
            conn.execute(text(stmt))

        with open(CSV_PATH, newline="") as f:
            reader = csv.DictReader(f)
            rows = []
            for row in reader:
                record = {}
                for c in COLUMNS:
                    val = row[c]
                    if val == "" and c in NULLABLE_STRING_COLS:
                        record[c] = None
                    elif c == "salary":
                        record[c] = int(val)
                    else:
                        record[c] = val
                rows.append(record)

        placeholders = ", ".join(f":{c}" for c in COLUMNS)
        conn.execute(
            text(f"INSERT INTO employees ({', '.join(COLUMNS)}) VALUES ({placeholders})"),
            rows,
        )

        count = conn.execute(text("SELECT COUNT(*) FROM employees")).scalar()

    dialect = "PostgreSQL" if is_postgres() else "SQLite"
    print(f"Loaded {count} employees into {dialect} ({engine.url.render_as_string(hide_password=True)})")

    # Invalidate the in-process employee DataFrame cache (app.analytics.data_access)
    # and the roster/department caches (app.governance.policies, app.api.nl_parser)
    # so a reload is picked up rather than serving stale cached data.
    from app.analytics.data_access import clear_cache as _clear_df_cache
    from app.governance.policies import _employee_names, _non_person_phrases
    from app.api.nl_parser import _known_departments

    _clear_df_cache()
    _employee_names.cache_clear()
    _non_person_phrases.cache_clear()
    _known_departments.cache_clear()


if __name__ == "__main__":
    main()
