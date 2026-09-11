"""
Validation layer — sits between the tool result and the AI's answer to the
analyst (see the architecture diagram: SQL/Analytics Tool -> Validation
Layer -> Governance Check -> AI Response).

This does NOT re-derive the number a second way (that would just be a second
opinion, not a check) — it sanity-checks the *shape* of the result against
what's structurally possible, which is exactly the class of error a
mis-parsed intent or a bug produces: a negative headcount, a 340% attrition
rate, an empty population silently averaged to zero, a metric_name that
doesn't match the tool that was called.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class ValidationResult:
    is_valid: bool
    notes: List[str] = field(default_factory=list)


_RATE_METRICS = {"calculate_attrition", "calculate_promotion_rate"}
_COUNT_METRICS = {"calculate_headcount", "calculate_new_hires"}
_MONEY_METRICS = {"calculate_average_salary"}
_YEARS_METRICS = {"calculate_average_tenure"}


def _validate_single(tool_name: str, result: dict) -> ValidationResult:
    notes = []
    value = result.get("value")
    unit = result.get("unit")

    if value is None:
        return ValidationResult(False, ["Result has no value."])

    if unit == "%":
        if not (0 <= value <= 100):
            notes.append(f"Rate {value}% is outside the valid 0-100% range.")
    elif unit == "count":
        if value < 0:
            notes.append(f"Count {value} is negative.")
    elif unit == "$":
        if value < 0:
            notes.append(f"Salary {value} is negative.")
        elif value > 0 and value < 10000:
            notes.append(f"Average salary {value} looks implausibly low — check filters.")
    elif unit == "years":
        if value < 0 or value > 60:
            notes.append(f"Tenure {value} years is outside a plausible range.")

    breakdown = result.get("breakdown", {})
    if tool_name in _RATE_METRICS:
        denom_keys = [k for k in breakdown if "headcount_at_period_start" in k]
        if denom_keys and breakdown.get(denom_keys[0]) == 0:
            notes.append("Denominator (headcount at period start) was zero — rate is undefined, not a true 0.")

    return ValidationResult(is_valid=(len(notes) == 0), notes=notes)


def validate_result(tool_name: str, result: dict) -> ValidationResult:
    # Composite/multi-metric tool results: validate each embedded metric.
    if "metric_name" not in result:
        all_notes = []
        for key in ("headcount", "trailing_12mo_attrition", "average_tenure", "average_salary"):
            sub = result.get(key)
            if isinstance(sub, dict) and "metric_name" in sub:
                sub_tool = {
                    "headcount": "calculate_headcount",
                    "trailing_12mo_attrition": "calculate_attrition",
                    "average_tenure": "calculate_average_tenure",
                    "average_salary": "calculate_average_salary",
                }[key]
                v = _validate_single(sub_tool, sub)
                if not v.is_valid:
                    all_notes.extend(f"{key}: {n}" for n in v.notes)
        return ValidationResult(is_valid=(len(all_notes) == 0), notes=all_notes)

    return _validate_single(tool_name, result)
