"""
Attrition metrics — encoded analytical method (Governed Attrition
Calculation v1.2).

    Attrition Rate (%) = (Employees who left in period / Average headcount
                           in period) x 100

    Average headcount = (headcount at period start + headcount at period end) / 2

This is intentionally NOT left to the LLM to compute. The formula, the
averaging convention, and the period boundaries are fixed here so that the
same question always produces the same number - that's what "encoded
analytical method" / "consistent and reproducible" means in practice.

v1.1 -> v1.2 changelog (kept for the "Explain this answer" audit trail):
    v1.1 used headcount at period END only as the denominator.
    v1.2 switched to AVERAGE of start/end headcount, which is the more
         standard HR formula and less sensitive to growth/shrinkage during
         the period.
"""

from datetime import date
from typing import Optional

import pandas as pd

from app.analytics.base import AnalyticsResult
from app.analytics.data_access import load_employees_df
from app.analytics.headcount import headcount_as_of

METHOD_NAME = "Governed Attrition Calculation"
METHOD_VERSION = "v1.2"


def calculate_attrition(
    department: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    termination_type: str = "all",  # "all" | "voluntary" | "involuntary"
) -> AnalyticsResult:
    df = load_employees_df()

    end = pd.to_datetime(end_date) if end_date else pd.Timestamp(date.today())
    start = pd.to_datetime(start_date) if start_date else (end - pd.DateOffset(months=12))

    left_mask = df["termination_date"].notna() & (df["termination_date"] >= start) & (df["termination_date"] <= end)
    if department:
        left_mask &= df["department"].str.lower() == department.lower()
    if termination_type != "all":
        left_mask &= df["termination_type"].str.lower() == termination_type.lower()

    leavers = int(left_mask.sum())

    hc_start = headcount_as_of(df, start, department)
    hc_end = headcount_as_of(df, end, department)
    avg_headcount = (hc_start + hc_end) / 2 if (hc_start + hc_end) > 0 else 0

    rate = round((leavers / avg_headcount) * 100, 1) if avg_headcount else 0.0

    scope = f"{department} department" if department else "company-wide"
    type_label = {"all": "Overall", "voluntary": "Voluntary", "involuntary": "Involuntary"}[termination_type]

    return AnalyticsResult(
        metric_name=f"{type_label} Attrition Rate ({scope})",
        value=rate,
        unit="%",
        method_name=METHOD_NAME,
        method_version=METHOD_VERSION,
        formula="Attrition Rate (%) = (Employees who left in period / Average headcount) x 100, "
                "where Average headcount = (headcount at period start + headcount at period end) / 2",
        inputs={
            "department": department,
            "start_date": start.date().isoformat(),
            "end_date": end.date().isoformat(),
            "termination_type": termination_type,
        },
        breakdown={
            "employees_who_left": leavers,
            "headcount_at_period_start": hc_start,
            "headcount_at_period_end": hc_end,
            "average_headcount": round(avg_headcount, 1),
        },
        filters_applied={"department": department, "termination_type": termination_type},
    )
