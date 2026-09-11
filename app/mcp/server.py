"""
People Analytics MCP Server.

This is the centerpiece of the project: the LLM never talks to PostgreSQL
(here, SQLite) directly. It only sees these six governed tools, each of
which routes through validation and governance before touching the
database:

    LLM -> MCP tool -> validation -> governance -> database -> result

Run standalone (stdio transport, for use with an MCP-compatible client / the
`mcp dev` inspector):

    python3 -m app.mcp.server

The FastAPI backend (app/api/main.py) calls the same tool functions directly
in-process for the Streamlit demo, rather than spinning up a separate MCP
client/server round trip — but the tool contracts and governance path are
identical either way, since app/api/main.py imports these tool functions
from the exact same modules registered below.
"""

from typing import Optional

from mcp.server.fastmcp import FastMCP

from app.mcp.tools import analytics_tools
from app.mcp.tools.sql_tool import run_governed_sql as _run_governed_sql

mcp = FastMCP(
    "people-analytics-copilot",
    instructions=(
        "Tools for governed People Analytics queries. Never expose employee "
        "full names, or raw (non-aggregated) salary or performance_rating "
        "values. Prefer the specific calculate_*/get_*_metrics tools; use "
        "run_governed_sql only for questions those tools cannot answer, and "
        "even then only write aggregate SELECT queries."
    ),
)


@mcp.tool()
def get_employee_metrics(as_of_date: Optional[str] = None) -> dict:
    """Company-wide snapshot: headcount, trailing-12mo attrition, average tenure, average salary by department."""
    return analytics_tools.get_employee_metrics(as_of_date)


@mcp.tool()
def get_department_metrics(department: str, as_of_date: Optional[str] = None) -> dict:
    """Snapshot for one department: headcount, trailing-12mo attrition, average tenure, average salary."""
    return analytics_tools.get_department_metrics(department, as_of_date)


@mcp.tool()
def calculate_attrition(
    department: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    termination_type: str = "all",
) -> dict:
    """Attrition rate (%) for a department or company-wide over a date range. termination_type: all|voluntary|involuntary."""
    return analytics_tools.calculate_attrition(department, start_date, end_date, termination_type)


@mcp.tool()
def calculate_headcount(department: Optional[str] = None, as_of_date: Optional[str] = None) -> dict:
    """Headcount for a department or company-wide as of a date."""
    return analytics_tools.calculate_headcount(department, as_of_date)


@mcp.tool()
def calculate_average_tenure(
    department: Optional[str] = None,
    as_of_date: Optional[str] = None,
    include_terminated: bool = False,
) -> dict:
    """Average employee tenure in years for a department or company-wide."""
    return analytics_tools.calculate_average_tenure(department, as_of_date, include_terminated)


@mcp.tool()
def calculate_new_hires(department: Optional[str] = None, start_date: Optional[str] = None,
                         end_date: Optional[str] = None) -> dict:
    """Number of new hires for a department or company-wide in a date range."""
    return analytics_tools.calculate_new_hires(department, start_date, end_date)


@mcp.tool()
def calculate_average_salary(department: Optional[str] = None, group_by: Optional[str] = None) -> dict:
    """Average salary (aggregate only, never per-employee) for a department, optionally grouped by department/job_level/location."""
    return analytics_tools.calculate_average_salary(department, group_by)


@mcp.tool()
def calculate_promotion_rate(department: Optional[str] = None, start_date: Optional[str] = None,
                              end_date: Optional[str] = None) -> dict:
    """Percentage of employees promoted during a date range, for a department or company-wide."""
    return analytics_tools.calculate_promotion_rate(department, start_date, end_date)


@mcp.tool()
def run_governed_sql(sql: str) -> dict:
    """
    Execute a governed, read-only SELECT against the employee dataset for
    questions the other tools don't cover. Blocked fields (full_name,
    manager) are always rejected; salary and performance_rating are only
    permitted wrapped in an aggregate function (AVG/SUM/MIN/MAX/COUNT).
    """
    return _run_governed_sql(sql)


if __name__ == "__main__":
    mcp.run()
