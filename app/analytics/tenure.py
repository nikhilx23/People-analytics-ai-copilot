"""
Tenure metrics — encoded analytical method (Governed Tenure Calculation v1.0).

    Average Tenure (years) = mean(as_of_date - hire_date) across active
                              employees, expressed in years (365.25 days)

Terminated employees are excluded by default, matching the common HR
convention of "average tenure" describing the current workforce. Set
include_terminated=True to include everyone, with terminated employees'
tenure measured through their termination date.
"""

from datetime import date
from typing import Optional

import pandas as pd

from app.analytics.base import AnalyticsResult
from app.analytics.data_access import load_employees_df

METHOD_NAME = "Governed Tenure Calculation"
METHOD_VERSION = "v1.0"

DAYS_PER_YEAR = 365.25


def calculate_average_tenure(
    department: Optional[str] = None,
    as_of_date: Optional[str] = None,
    include_terminated: bool = False,
) -> AnalyticsResult:
    df = load_employees_df()
    as_of = pd.to_datetime(as_of_date) if as_of_date else pd.Timestamp(date.today())

    if include_terminated:
        subset = df.copy()
    else:
        subset = df[df["employee_status"] == "Active"].copy()

    if department:
        subset = subset[subset["department"].str.lower() == department.lower()]

    end_dates = subset["termination_date"].fillna(as_of) if include_terminated else as_of
    tenure_days = (end_dates - subset["hire_date"]).dt.days
    tenure_years = tenure_days / DAYS_PER_YEAR

    n = len(subset)
    avg_years = round(float(tenure_years.mean()), 2) if n else 0.0

    scope = f"{department} department" if department else "company-wide"
    pop_label = "all employees (active + terminated)" if include_terminated else "active employees"

    return AnalyticsResult(
        metric_name=f"Average Tenure ({scope})",
        value=avg_years,
        unit="years",
        method_name=METHOD_NAME,
        method_version=METHOD_VERSION,
        formula="Average Tenure (years) = MEAN(as_of_date - hire_date) / 365.25, over " + pop_label,
        inputs={"department": department, "as_of_date": as_of.date().isoformat(), "include_terminated": include_terminated},
        breakdown={"employees_included": n, "average_tenure_years": avg_years},
        filters_applied={"department": department},
    )
