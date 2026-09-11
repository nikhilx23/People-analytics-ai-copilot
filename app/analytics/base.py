"""
Shared result type for every encoded analytical method.

Every calculate_* / get_* function in app/analytics returns an
AnalyticsResult, not a bare number. That's deliberate: it's what powers the
"Explain this answer" feature (methodology, formula, inputs, and data source
are attached to the answer itself, not reconstructed after the fact) and
what makes results reproducible - the same question always resolves to the
same versioned method.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class AnalyticsResult:
    metric_name: str            # e.g. "Voluntary Attrition Rate"
    value: Any                  # the headline number, e.g. 8.7
    unit: str                   # "%", "years", "count", "$"
    method_name: str            # e.g. "Governed Attrition Calculation"
    method_version: str         # e.g. "v1.2"
    formula: str                # human-readable formula
    inputs: dict = field(default_factory=dict)     # named parameters used
    breakdown: dict = field(default_factory=dict)  # intermediate values (numerator, denominator, ...)
    data_source: str = "Employee Analytics Dataset (governed view)"
    computed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    filters_applied: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "metric_name": self.metric_name,
            "value": self.value,
            "unit": self.unit,
            "method_name": self.method_name,
            "method_version": self.method_version,
            "formula": self.formula,
            "inputs": self.inputs,
            "breakdown": self.breakdown,
            "data_source": self.data_source,
            "computed_at": self.computed_at,
            "filters_applied": self.filters_applied,
        }

    def headline(self) -> str:
        if self.unit == "%":
            return f"{self.value}%"
        if self.unit == "$":
            return f"${self.value:,.0f}"
        if self.unit == "years":
            return f"{self.value} years"
        return str(self.value)

    def explain(self) -> str:
        """Plain-text 'Explain this answer' breakdown."""
        lines = [
            f"Answer: {self.metric_name} = {self.headline()}",
            "",
            f"Method: {self.method_name} {self.method_version}",
            f"Formula: {self.formula}",
        ]
        if self.filters_applied:
            filt = ", ".join(f"{k}={v}" for k, v in self.filters_applied.items() if v is not None)
            if filt:
                lines.append(f"Filters: {filt}")
        if self.breakdown:
            lines.append("")
            lines.append("Breakdown:")
            for k, v in self.breakdown.items():
                lines.append(f"  {k}: {v}")
        lines.append("")
        lines.append(f"Data source: {self.data_source}")
        lines.append(f"Computed at: {self.computed_at}")
        return "\n".join(lines)
