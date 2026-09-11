"""
Compensation & promotion metrics — encoded analytical methods.

Salary is a sensitive field: this module NEVER returns a per-employee salary,
only aggregates (mean, grouped means). That guarantee is enforced in two
places independently: this module simply never selects a raw salary column
into its output, and the SQL/tool governance layer (app/governance) would
reject an individual-level salary query even if something upstream tried.

    Average Salary Calculation v1.0:
        Average Salary = MEAN(salary) over the matching population

    Promotion Rate Calculation v1.0:
        Promotion Rate (%) = (Employees promoted in period /
                               Headcount at period start) x 100
"""

from datetime import date
from typing import Optional

import pandas as pd

from app.analytics.base import AnalyticsResult
from app.analytics.data_access import load_employees_df
from app.analytics.headcount import headcount_as_of

SALARY_METHOD_NAME = "Governed Average Salary Calculation"
SALARY_METHOD_VERSION = "v1.0"

PROMO_METHOD_NAME = "Governed Promotion Rate Calculation"
PROMO_METHOD_VERSION = "v1.0"


def calculate_average_salary(
    department: Optional[str] = None,
    group_by: Optional[str] = None,  # None | "department" | "job_level" | "location"
    active_only: bool = True,
) -> AnalyticsResult:
    df = load_employees_df()
    subset = df[df["employee_status"] == "Active"] if active_only else df

    if department:
        subset = subset[subset["department"].str.lower() == department.lower()]

    scope = f"{department} department" if department else "company-wide"

    if group_by and group_by in ("department", "job_level", "location"):
        grouped = subset.groupby(group_by)["salary"].mean().round(0).astype(int)
        breakdown = {k: int(v) for k, v in grouped.sort_values(ascending=False).items()}
        overall = int(round(subset["salary"].mean(), 0)) if len(subset) else 0
        return AnalyticsResult(
            metric_name=f"Average Salary by {group_by.replace('_', ' ').title()} ({scope})",
            value=overall,
            unit="$",
            method_name=SALARY_METHOD_NAME,
            method_version=SALARY_METHOD_VERSION,
            formula=f"Average Salary = MEAN(salary), grouped by {group_by}",
            inputs={"department": department, "group_by": group_by, "active_only": active_only},
            breakdown=breakdown,
            filters_applied={"department": department},
        )

    avg_salary = int(round(subset["salary"].mean(), 0)) if len(subset) else 0
    return AnalyticsResult(
        metric_name=f"Average Salary ({scope})",
        value=avg_salary,
        unit="$",
        method_name=SALARY_METHOD_NAME,
        method_version=SALARY_METHOD_VERSION,
        formula="Average Salary = MEAN(salary) over matching population",
        inputs={"department": department, "active_only": active_only},
        breakdown={"employees_included": int(len(subset)), "average_salary": avg_salary},
        filters_applied={"department": department},
    )


def calculate_promotion_rate(
    department: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> AnalyticsResult:
    df = load_employees_df()
    end = pd.to_datetime(end_date) if end_date else pd.Timestamp(date.today())
    start = pd.to_datetime(start_date) if start_date else (end - pd.DateOffset(months=12))

    promo_mask = df["last_promotion_date"].notna() & (df["last_promotion_date"] >= start) & (df["last_promotion_date"] <= end)
    if department:
        promo_mask &= df["department"].str.lower() == department.lower()

    promoted = int(promo_mask.sum())
    hc_start = headcount_as_of(df, start, department)

    rate = round((promoted / hc_start) * 100, 1) if hc_start else 0.0
    scope = f"{department} department" if department else "company-wide"

    return AnalyticsResult(
        metric_name=f"Promotion Rate ({scope})",
        value=rate,
        unit="%",
        method_name=PROMO_METHOD_NAME,
        method_version=PROMO_METHOD_VERSION,
        formula="Promotion Rate (%) = (Employees promoted in period / Headcount at period start) x 100",
        inputs={"department": department, "start_date": start.date().isoformat(), "end_date": end.date().isoformat()},
        breakdown={
            "employees_promoted": promoted,
            "headcount_at_period_start": hc_start,
        },
        filters_applied={"department": department},
    )
