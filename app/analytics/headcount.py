"""
Headcount metrics — encoded analytical methods (Governed Headcount
Calculation v1.0).

Headcount as of a date = employees hired on/before that date whose
termination date (if any) is after that date.
"""

from datetime import date, datetime
from typing import Optional

import pandas as pd

from app.analytics.base import AnalyticsResult
from app.analytics.data_access import load_employees_df

METHOD_NAME = "Governed Headcount Calculation"
METHOD_VERSION = "v1.0"


def _as_of_mask(df: pd.DataFrame, as_of: pd.Timestamp) -> pd.Series:
    hired = df["hire_date"] <= as_of
    still_here = df["termination_date"].isna() | (df["termination_date"] > as_of)
    return hired & still_here


def headcount_as_of(df: pd.DataFrame, as_of: pd.Timestamp, department: Optional[str] = None) -> int:
    """Raw integer helper used internally (also by attrition.py)."""
    mask = _as_of_mask(df, as_of)
    if department:
        mask &= df["department"].str.lower() == department.lower()
    return int(mask.sum())


def calculate_headcount(department: Optional[str] = None, as_of_date: Optional[str] = None) -> AnalyticsResult:
    df = load_employees_df()
    as_of = pd.to_datetime(as_of_date) if as_of_date else pd.Timestamp(date.today())

    count = headcount_as_of(df, as_of, department)

    scope = f"{department} department" if department else "company-wide"
    return AnalyticsResult(
        metric_name=f"Headcount ({scope})",
        value=count,
        unit="count",
        method_name=METHOD_NAME,
        method_version=METHOD_VERSION,
        formula="Headcount = employees with hire_date <= as_of_date AND "
                "(termination_date IS NULL OR termination_date > as_of_date)",
        inputs={"department": department, "as_of_date": as_of.date().isoformat()},
        breakdown={"as_of_date": as_of.date().isoformat(), "matching_employees": count},
        filters_applied={"department": department},
    )


def calculate_new_hires(department: Optional[str] = None, start_date: Optional[str] = None,
                         end_date: Optional[str] = None) -> AnalyticsResult:
    df = load_employees_df()
    end = pd.to_datetime(end_date) if end_date else pd.Timestamp(date.today())
    start = pd.to_datetime(start_date) if start_date else (end - pd.DateOffset(years=1))

    mask = (df["hire_date"] >= start) & (df["hire_date"] <= end)
    if department:
        mask &= df["department"].str.lower() == department.lower()

    count = int(mask.sum())
    scope = f"{department} department" if department else "company-wide"

    return AnalyticsResult(
        metric_name=f"New Hires ({scope})",
        value=count,
        unit="count",
        method_name=METHOD_NAME,
        method_version=METHOD_VERSION,
        formula="New Hires = COUNT(employees WHERE start_date <= hire_date <= end_date)",
        inputs={"department": department, "start_date": start.date().isoformat(), "end_date": end.date().isoformat()},
        breakdown={"period_start": start.date().isoformat(), "period_end": end.date().isoformat(), "new_hires": count},
        filters_applied={"department": department},
    )
