"""
Independent reference calculations for the evaluation harness.

Deliberately does NOT import app.analytics or app.mcp — if the eval runner
reused the same formulas it's testing, a bug in those formulas would always
"pass". This module recomputes the same metrics a second, independent way
(pandas directly against the raw dataset) so the eval harness is actually
checking "did the AI's answer match the data", not just "did the code run".
"""

from datetime import date

import pandas as pd

from app.database.connection import get_connection


def _load_raw() -> pd.DataFrame:
    conn = get_connection()
    try:
        df = pd.read_sql_query("SELECT * FROM employees", conn)
    finally:
        conn.close()
    for col in ("hire_date", "termination_date", "last_promotion_date"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def ref_headcount(department=None, as_of=None) -> int:
    df = _load_raw()
    as_of = pd.Timestamp(as_of) if as_of else pd.Timestamp(date.today())
    mask = (df["hire_date"] <= as_of) & (df["termination_date"].isna() | (df["termination_date"] > as_of))
    if department:
        mask &= df["department"] == department
    return int(mask.sum())


def ref_attrition(department=None, start=None, end=None, term_type="all") -> float:
    df = _load_raw()
    end = pd.Timestamp(end) if end else pd.Timestamp(date.today())
    start = pd.Timestamp(start) if start else (end - pd.DateOffset(months=12))

    left_mask = df["termination_date"].notna() & (df["termination_date"] >= start) & (df["termination_date"] <= end)
    if department:
        left_mask &= df["department"] == department
    if term_type != "all":
        left_mask &= df["termination_type"].str.lower() == term_type

    leavers = int(left_mask.sum())
    hc_start = ref_headcount(department, start)
    hc_end = ref_headcount(department, end)
    avg_hc = (hc_start + hc_end) / 2
    return round((leavers / avg_hc) * 100, 1) if avg_hc else 0.0


def ref_new_hires(department=None, start=None, end=None) -> int:
    df = _load_raw()
    end = pd.Timestamp(end) if end else pd.Timestamp(date.today())
    start = pd.Timestamp(start) if start else (end - pd.DateOffset(years=1))
    mask = (df["hire_date"] >= start) & (df["hire_date"] <= end)
    if department:
        mask &= df["department"] == department
    return int(mask.sum())


def ref_average_tenure(department=None, as_of=None) -> float:
    df = _load_raw()
    as_of = pd.Timestamp(as_of) if as_of else pd.Timestamp(date.today())
    subset = df[df["employee_status"] == "Active"]
    if department:
        subset = subset[subset["department"] == department]
    tenure_years = (as_of - subset["hire_date"]).dt.days / 365.25
    return round(float(tenure_years.mean()), 2) if len(subset) else 0.0


def ref_average_salary(department=None) -> int:
    df = _load_raw()
    subset = df[df["employee_status"] == "Active"]
    if department:
        subset = subset[subset["department"] == department]
    return int(round(subset["salary"].mean(), 0)) if len(subset) else 0


def ref_promotion_rate(department=None, start=None, end=None) -> float:
    df = _load_raw()
    end = pd.Timestamp(end) if end else pd.Timestamp(date.today())
    start = pd.Timestamp(start) if start else (end - pd.DateOffset(months=12))
    mask = df["last_promotion_date"].notna() & (df["last_promotion_date"] >= start) & (df["last_promotion_date"] <= end)
    if department:
        mask &= df["department"] == department
    promoted = int(mask.sum())
    hc_start = ref_headcount(department, start)
    return round((promoted / hc_start) * 100, 1) if hc_start else 0.0
