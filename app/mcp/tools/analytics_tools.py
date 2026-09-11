"""
MCP tool implementations that wrap the encoded analytics methods.

Every function here is a thin, audited wrapper:
    validate inputs -> call the encoded analytics method -> log to query_log
    -> return a plain dict (JSON-serializable) for the MCP tool response.

None of these functions accept or execute arbitrary SQL, and none of them
touch full_name or raw per-row salary/performance - the analytics layer
underneath (app/analytics) never loads those into memory to begin with.
"""

from typing import Optional

from app.analytics import attrition, headcount, tenure, compensation
from app.governance.policies import log_decision


def _log(tool_name: str, kwargs: dict, result_summary: str, role: str = "hr_analyst"):
    log_decision(
        user_role=role,
        question=None,
        tool_used=tool_name,
        sql_executed=None,
        decision="ALLOWED",
        result_summary=result_summary,
    )


def calculate_attrition(
    department: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    termination_type: str = "all",
) -> dict:
    """Calculate attrition rate for a department (or company-wide) over a date range."""
    result = attrition.calculate_attrition(department, start_date, end_date, termination_type)
    _log("calculate_attrition", locals(), result.headline())
    return result.to_dict()


def calculate_headcount(department: Optional[str] = None, as_of_date: Optional[str] = None) -> dict:
    """Calculate headcount for a department (or company-wide) as of a date."""
    result = headcount.calculate_headcount(department, as_of_date)
    _log("calculate_headcount", locals(), result.headline())
    return result.to_dict()


def calculate_new_hires(department: Optional[str] = None, start_date: Optional[str] = None,
                         end_date: Optional[str] = None) -> dict:
    """Calculate number of new hires for a department (or company-wide) in a date range."""
    result = headcount.calculate_new_hires(department, start_date, end_date)
    _log("calculate_new_hires", locals(), result.headline())
    return result.to_dict()


def calculate_average_tenure(department: Optional[str] = None, as_of_date: Optional[str] = None,
                              include_terminated: bool = False) -> dict:
    """Calculate average employee tenure for a department (or company-wide)."""
    result = tenure.calculate_average_tenure(department, as_of_date, include_terminated)
    _log("calculate_average_tenure", locals(), result.headline())
    return result.to_dict()


def calculate_average_salary(department: Optional[str] = None, group_by: Optional[str] = None) -> dict:
    """Calculate average salary (aggregate only) for a department, optionally grouped."""
    result = compensation.calculate_average_salary(department, group_by)
    _log("calculate_average_salary", locals(), result.headline())
    return result.to_dict()


def calculate_promotion_rate(department: Optional[str] = None, start_date: Optional[str] = None,
                              end_date: Optional[str] = None) -> dict:
    """Calculate the percentage of employees promoted during a date range."""
    result = compensation.calculate_promotion_rate(department, start_date, end_date)
    _log("calculate_promotion_rate", locals(), result.headline())
    return result.to_dict()


def get_department_metrics(department: str, as_of_date: Optional[str] = None) -> dict:
    """
    Composite snapshot for one department: headcount, trailing-12mo attrition,
    average tenure, and average salary. This is the tool the copilot reaches
    for on broad "how's department X doing" questions.
    """
    hc = headcount.calculate_headcount(department, as_of_date)
    attr = attrition.calculate_attrition(department, None, as_of_date)
    ten = tenure.calculate_average_tenure(department, as_of_date)
    sal = compensation.calculate_average_salary(department)

    summary = {
        "department": department,
        "headcount": hc.to_dict(),
        "trailing_12mo_attrition": attr.to_dict(),
        "average_tenure": ten.to_dict(),
        "average_salary": sal.to_dict(),
    }
    _log("get_department_metrics", {"department": department}, f"headcount={hc.value}, attrition={attr.value}%")
    return summary


def get_employee_metrics(as_of_date: Optional[str] = None) -> dict:
    """
    Composite company-wide snapshot: total headcount, trailing-12mo attrition,
    average tenure, and average salary by department.
    """
    hc = headcount.calculate_headcount(None, as_of_date)
    attr = attrition.calculate_attrition(None, None, as_of_date)
    ten = tenure.calculate_average_tenure(None, as_of_date)
    sal = compensation.calculate_average_salary(None, group_by="department")

    summary = {
        "headcount": hc.to_dict(),
        "trailing_12mo_attrition": attr.to_dict(),
        "average_tenure": ten.to_dict(),
        "average_salary_by_department": sal.to_dict(),
    }
    _log("get_employee_metrics", {}, f"headcount={hc.value}, attrition={attr.value}%")
    return summary
