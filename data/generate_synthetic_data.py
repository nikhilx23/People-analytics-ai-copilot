"""
Synthetic Employee Data Generator
----------------------------------
Generates a fictional, internally-consistent employee dataset for the
People Analytics AI Copilot project. All employees, names, and events
are fabricated for demo purposes only.

Run:
    python3 generate_synthetic_data.py

Output:
    synthetic_employee_data.csv  (all fields, including PII - "raw" table,
                                   access to this is governed)
"""

import csv
import random
from datetime import date, timedelta

from faker import Faker

fake = Faker()
Faker.seed(42)
random.seed(42)

N_EMPLOYEES = 520
TODAY = date(2026, 8, 31)  # matches "today" for this project
START_WINDOW = date(2019, 1, 1)  # earliest possible hire date

DEPARTMENTS = {
    "Engineering": ["Software Engineer", "Senior Engineer", "Staff Engineer", "Engineering Manager", "QA Engineer"],
    "Sales": ["Account Executive", "Sales Development Rep", "Sales Manager", "Regional Sales Director"],
    "Marketing": ["Marketing Specialist", "Content Strategist", "Marketing Manager", "Brand Manager"],
    "Customer Support": ["Support Associate", "Senior Support Associate", "Support Team Lead"],
    "Finance": ["Financial Analyst", "Accountant", "Finance Manager", "Controller"],
    "Human Resources": ["HR Generalist", "Recruiter", "HR Business Partner", "HR Manager"],
    "Product": ["Product Manager", "Senior Product Manager", "Product Analyst"],
    "Operations": ["Operations Analyst", "Operations Manager", "Logistics Coordinator"],
}

JOB_LEVELS = ["L1", "L2", "L3", "L4", "L5", "M1", "M2"]

LOCATIONS = [
    "Austin, TX", "New York, NY", "San Francisco, CA", "Chicago, IL",
    "Remote - US", "London, UK", "Toronto, CA", "Denver, CO",
]

# Base salary bands per department (rough, fictional, for realistic variance)
SALARY_BANDS = {
    "Engineering": (95000, 190000),
    "Sales": (70000, 160000),
    "Marketing": (65000, 140000),
    "Customer Support": (48000, 85000),
    "Finance": (70000, 150000),
    "Human Resources": (60000, 130000),
    "Product": (90000, 180000),
    "Operations": (55000, 120000),
}

TERMINATION_REASONS_VOLUNTARY = [
    "Resigned - New Opportunity", "Resigned - Relocation", "Resigned - Career Change",
    "Retirement",
]
TERMINATION_REASONS_INVOLUNTARY = [
    "Involuntary - Performance", "Involuntary - Restructuring", "Involuntary - Policy Violation",
]


def random_date(start: date, end: date) -> date:
    delta_days = (end - start).days
    if delta_days <= 0:
        return start
    return start + timedelta(days=random.randint(0, delta_days))


def make_employee(emp_id: int, managers_pool: dict) -> dict:
    department = random.choice(list(DEPARTMENTS.keys()))
    title = random.choice(DEPARTMENTS[department])
    level = random.choice(JOB_LEVELS)
    location = random.choice(LOCATIONS)

    hire_date = random_date(START_WINDOW, TODAY - timedelta(days=30))

    low, high = SALARY_BANDS[department]
    # Level nudges salary up a bit
    level_bump = {"L1": 0.85, "L2": 1.0, "L3": 1.15, "L4": 1.3, "L5": 1.45, "M1": 1.35, "M2": 1.55}[level]
    salary = int(random.uniform(low, high) * min(level_bump, 1.3))

    performance = random.choices(
        ["Exceeds Expectations", "Meets Expectations", "Below Expectations", "Outstanding"],
        weights=[0.22, 0.55, 0.10, 0.13],
    )[0]

    manager = managers_pool.get(department, "Unassigned")

    # Attrition modeling: ~13% annualized voluntary, ~4% involuntary, skewed toward
    # lower performance / newer tenure for involuntary, and slightly higher for
    # long-tenured "flight risk" profiles for voluntary - just enough realism to
    # make department comparisons meaningful.
    tenure_days = (TODAY - hire_date).days
    tenure_years = tenure_days / 365.25

    # Promotion history: employees with >= 1 year tenure have a chance of one
    # promotion event somewhere between 1 year after hire and today (or their
    # termination date, whichever is earlier - handled after we know that).
    last_promotion_date = ""
    if tenure_years >= 1.0 and random.random() < 0.35:
        promo_earliest = hire_date + timedelta(days=365)
        if promo_earliest < TODAY:
            last_promotion_date = random_date(promo_earliest, TODAY).isoformat()

    terminated = False
    termination_date = ""
    termination_type = ""
    termination_reason = ""

    voluntary_chance = 0.16 if tenure_years > 1.5 else 0.08
    involuntary_chance = 0.03 if performance != "Below Expectations" else 0.15

    roll = random.random()
    if roll < involuntary_chance:
        terminated = True
        termination_type = "Involuntary"
        termination_reason = random.choice(TERMINATION_REASONS_INVOLUNTARY)
    elif roll < involuntary_chance + voluntary_chance:
        terminated = True
        termination_type = "Voluntary"
        termination_reason = random.choice(TERMINATION_REASONS_VOLUNTARY)

    if terminated:
        earliest_term = hire_date + timedelta(days=90)
        if earliest_term >= TODAY:
            terminated = False
            termination_type = ""
            termination_reason = ""
        else:
            term_date = random_date(earliest_term, TODAY)
            termination_date = term_date.isoformat()
            # A promotion can't happen after someone left.
            if last_promotion_date and last_promotion_date > termination_date:
                last_promotion_date = ""

    return {
        "employee_id": f"E{emp_id:05d}",
        "full_name": fake.name(),
        "department": department,
        "job_title": title,
        "job_level": level,
        "location": location,
        "hire_date": hire_date.isoformat(),
        "termination_date": termination_date,
        "termination_type": termination_type,
        "termination_reason": termination_reason,
        "employee_status": "Terminated" if terminated else "Active",
        "salary": salary,
        "performance_rating": performance,
        "manager": manager,
        "last_promotion_date": last_promotion_date,
    }


def build_managers_pool() -> dict:
    return {dept: fake.name() for dept in DEPARTMENTS}


def main():
    managers_pool = build_managers_pool()
    rows = [make_employee(i + 1, managers_pool) for i in range(N_EMPLOYEES)]

    fieldnames = [
        "employee_id", "full_name", "department", "job_title", "job_level",
        "location", "hire_date", "termination_date", "termination_type",
        "termination_reason", "employee_status", "salary", "performance_rating",
        "manager", "last_promotion_date",
    ]

    out_path = "synthetic_employee_data.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    active = sum(1 for r in rows if r["employee_status"] == "Active")
    terminated = len(rows) - active
    print(f"Wrote {len(rows)} employees to {out_path}")
    print(f"  Active: {active}  Terminated: {terminated}")


if __name__ == "__main__":
    main()
