"""
Lightweight per-request DB-time accumulator.

The pipeline's "tool execution" stage is mostly pandas/SQL work happening
several call-frames down (app.analytics.data_access, app.mcp.tools.sql_tool)
— there's no clean single choke point to time it from the outside. Instead,
the DB-touching call sites themselves add their elapsed time here, and
app.api.copilot reads the accumulated total after the tool call completes.

Uses threading.local (not a module global) so concurrent requests — several
Streamlit sessions, or FastAPI handling requests on different threads —
don't add each other's DB time.
"""

import threading

_local = threading.local()


def reset():
    _local.db_time_ms = 0.0


def add_db_time(ms: float):
    if not hasattr(_local, "db_time_ms"):
        _local.db_time_ms = 0.0
    _local.db_time_ms += ms


def get_db_time_ms() -> float:
    return getattr(_local, "db_time_ms", 0.0)
