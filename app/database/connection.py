"""
Database connection helper — SQLAlchemy-backed so the same call sites work
against SQLite (zero-setup dev/demo) or PostgreSQL (production target).

Everything else in the app talks to the database through this one module.
Swapping dialects is a DATABASE_URL environment variable, not a rewrite:

    # SQLite (default, zero setup)
    unset DATABASE_URL

    # PostgreSQL
    export DATABASE_URL="postgresql+psycopg2://copilot_app:copilot_dev_pw@localhost:5432/people_analytics"

Callers get a SQLAlchemy Connection from db_session()/get_connection() and
run parameterized queries with sqlalchemy.text() and named (:param) binds,
which both dialects support identically. Query results are pulled through
.mappings() so callers can do row["column_name"] regardless of dialect.
"""

import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "employee_analytics.db"
DEFAULT_SQLITE_URL = f"sqlite:///{DB_PATH}"

DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_SQLITE_URL)

_engine: Engine = create_engine(DATABASE_URL, future=True)


def get_engine() -> Engine:
    return _engine


def is_postgres() -> bool:
    return _engine.dialect.name == "postgresql"


def get_connection() -> Connection:
    """Autocommitting connection for simple one-off reads/writes. Caller must .close()."""
    return _engine.connect().execution_options(isolation_level="AUTOCOMMIT")


@contextmanager
def db_session():
    """Context manager yielding a Connection inside a transaction, committing on success."""
    with _engine.begin() as conn:
        yield conn
