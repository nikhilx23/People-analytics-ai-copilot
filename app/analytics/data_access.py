"""
Governed data access for analytics methods.

Analytics functions never touch full_name (or raw per-row salary/performance
outside an aggregate) - they load a de-identified frame straight from the
database. This means the aggregate-only guarantee holds even if an analytics
function has a bug, because the direct-identifier column was never loaded
into memory in the first place - defense in depth alongside the SQL guard in
governance/policies.py.

Performance note: this used to run a fresh SELECT + pandas date-parse on
EVERY call, and composite tools (get_department_metrics,
get_employee_metrics) call four analytics functions each — so one "how's
Engineering doing" question was doing 4 redundant full-table loads. The
dataset is static for the life of the process (nothing here ever mutates the
cached frame in place — every caller filters/copies), so it's cached after
the first load. See app/api/performance.py for the measured before/after.
"""

import time

import pandas as pd
from sqlalchemy import text

from app.api import perf_context
from app.database.connection import get_engine

# full_name and manager are excluded at the source - analytics code operates
# on a de-identified frame by construction.
GOVERNED_COLUMNS = [
    "employee_id", "department", "job_title", "job_level", "location",
    "hire_date", "termination_date", "termination_type", "termination_reason",
    "employee_status", "salary", "performance_rating", "last_promotion_date",
]

_df_cache: pd.DataFrame = None


def load_employees_df() -> pd.DataFrame:
    global _df_cache
    if _df_cache is not None:
        return _df_cache

    started = time.perf_counter()
    engine = get_engine()
    with engine.connect() as conn:
        df = pd.read_sql_query(
            text(f"SELECT {', '.join(GOVERNED_COLUMNS)} FROM employees"), conn
        )

    for col in ("hire_date", "termination_date", "last_promotion_date"):
        df[col] = pd.to_datetime(df[col], errors="coerce")

    perf_context.add_db_time((time.perf_counter() - started) * 1000)

    _df_cache = df
    return _df_cache


def clear_cache():
    """Call after reloading/mutating the underlying table (e.g. in
    app.database.load_data, or between tests that reseed the dataset)."""
    global _df_cache
    _df_cache = None
