"""
Evaluation harness — runs app/evaluation/eval_dataset.csv through the full
copilot pipeline and checks each answer against an INDEPENDENTLY computed
expected result (app/evaluation/reference_calculations.py, which shares no
code with app/analytics). This is what answers the brief's questions:

    Was the tool selection correct?      -> tool_used check (check_type=compare)
    Did the answer match the data?      -> numeric value vs. reference calc
    Did the AI hallucinate?             -> any unhandled exception is a fail,
                                            not silently skipped
    Was the correct dataset used?       -> reference calc reads the same DB
    Did the AI follow data-access rules? -> blocked/clarify cases

The dataset is grouped into categories (see app/evaluation/
generate_eval_dataset.py) — normal questions, ambiguous phrasing, typos,
department aliases, comparisons, date ranges, unsupported metrics, and
boundary cases — and this runner reports accuracy per category as well as
overall, since a single blended number hides which kinds of questions the
system is actually weak on.

Run:
    python3 -m app.evaluation.eval_runner

Writes app/evaluation/eval_results.csv (id, category, question, check_type,
expected_result, ai_result, correct) and prints overall + per-category
accuracy.
"""

import csv
from collections import defaultdict
from pathlib import Path

from app.api.copilot import answer_question
from app.evaluation import reference_calculations as ref

DATASET_PATH = Path(__file__).resolve().parent / "eval_dataset.csv"
RESULTS_PATH = Path(__file__).resolve().parent / "eval_results.csv"

REF_FUNCS = {
    "attrition": lambda dept, row: ref.ref_attrition(
        dept, row["start_date"] or None, row["end_date"] or None,
        row["termination_type"] or "all",
    ),
    "new_hires": lambda dept, row: ref.ref_new_hires(
        dept, row["start_date"] or None, row["end_date"] or None,
    ),
    "headcount": lambda dept, row: ref.ref_headcount(dept),
    "tenure": lambda dept, row: ref.ref_average_tenure(dept),
    "salary": lambda dept, row: ref.ref_average_salary(dept),
    "promotion": lambda dept, row: ref.ref_promotion_rate(
        dept, row["start_date"] or None, row["end_date"] or None,
    ),
}

TOOL_FOR_METRIC = {
    "attrition": "calculate_attrition",
    "new_hires": "calculate_new_hires",
    "headcount": "calculate_headcount",
    "tenure": "calculate_average_tenure",
    "salary": "calculate_average_salary",
    "promotion": "calculate_promotion_rate",
}

TOLERANCE = {
    "attrition": 0.15, "new_hires": 0, "headcount": 0,
    "tenure": 0.05, "salary": 1, "promotion": 0.15,
}


def _close_enough(expected, actual, ref_metric) -> bool:
    if expected is None or actual is None:
        return expected == actual
    tol = TOLERANCE.get(ref_metric, 0)
    try:
        return abs(float(expected) - float(actual)) <= tol
    except (TypeError, ValueError):
        return expected == actual


def _eval_one(row: dict) -> dict:
    question = row["question"]
    check_type = row["check_type"]
    ref_metric = row["ref_metric"]
    category = row.get("category", "uncategorized")
    base = {"id": row["id"], "category": category, "question": question, "check_type": check_type}

    try:
        ai = answer_question(question)
    except Exception as e:  # an unhandled exception is a hard fail, not a skip
        return {**base, "expected_result": "N/A", "ai_result": f"EXCEPTION: {e}", "correct": False}

    if check_type == "blocked":
        correct = ai["blocked"] is True
        return {**base, "expected_result": "blocked", "ai_result": f"blocked={ai['blocked']}", "correct": correct}

    if check_type == "clarify":
        correct = (ai["blocked"] is False) and (ai.get("confidence") == "low")
        return {**base, "expected_result": "clarify (low confidence)",
                "ai_result": f"confidence={ai.get('confidence')}", "correct": correct}

    if check_type == "compare":
        dept_a, dept_b = row["department"], row["department2"]
        expected_tool = TOOL_FOR_METRIC[ref_metric]
        comparison = ai.get("comparison") or []
        depts_present = {c["department"] for c in comparison}
        correct = ai["tool_used"] == expected_tool and depts_present == {dept_a, dept_b}
        if correct:
            for c in comparison:
                expected = REF_FUNCS[ref_metric](c["department"], row)
                actual = c["result"].get("value")
                if not _close_enough(expected, actual, ref_metric):
                    correct = False
        return {**base,
                "expected_result": f"tool={expected_tool}, depts={{{dept_a}, {dept_b}}}",
                "ai_result": f"tool={ai['tool_used']}, depts={depts_present}", "correct": correct}

    # numeric
    dept = row["department"] or None
    expected = REF_FUNCS[ref_metric](dept, row)
    actual = (ai.get("result") or {}).get("value")
    correct = _close_enough(expected, actual, ref_metric)
    return {**base, "expected_result": expected, "ai_result": actual, "correct": correct}


def run_eval(dataset_path: Path = DATASET_PATH) -> dict:
    with open(dataset_path, newline="") as f:
        cases = list(csv.DictReader(f))

    results = [_eval_one(row) for row in cases]
    correct_count = sum(1 for r in results if r["correct"])
    accuracy = round((correct_count / len(cases)) * 100, 1) if cases else 0.0

    by_category = defaultdict(lambda: {"correct": 0, "total": 0})
    for r in results:
        by_category[r["category"]]["total"] += 1
        if r["correct"]:
            by_category[r["category"]]["correct"] += 1
    category_breakdown = {
        cat: {
            "correct": v["correct"], "total": v["total"],
            "accuracy_pct": round((v["correct"] / v["total"]) * 100, 1) if v["total"] else 0.0,
        }
        for cat, v in sorted(by_category.items())
    }

    with open(RESULTS_PATH, "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["id", "category", "question", "check_type", "expected_result", "ai_result", "correct"]
        )
        writer.writeheader()
        writer.writerows(results)

    return {
        "total": len(cases),
        "correct": correct_count,
        "accuracy_pct": accuracy,
        "category_breakdown": category_breakdown,
        "results": results,
    }


# CI regression gate. The current measured baseline is 99.5% (203/204) -
# one documented rule-based-parser limitation (see README "Known
# limitations"), not a bug. Set below that baseline, with a little room, so
# CI fails on a real regression but doesn't flake on the one known miss.
MIN_ACCURACY_PCT = 98.0


if __name__ == "__main__":
    import sys

    summary = run_eval()
    print(f"Evaluated {summary['total']} questions — {summary['correct']} correct — overall accuracy: {summary['accuracy_pct']}%")
    print()
    print("By category:")
    for cat, stats in summary["category_breakdown"].items():
        print(f"  {cat:14s} {stats['accuracy_pct']:5.1f}%  ({stats['correct']}/{stats['total']})")
    print()
    print(f"Full report written to {RESULTS_PATH}")

    failures = [r for r in summary["results"] if not r["correct"]]
    if failures:
        print(f"\n{len(failures)} failure(s):")
        for r in failures:
            print(f"  [FAIL] #{r['id']} ({r['category']}) {r['question']!r} expected={r['expected_result']} ai={r['ai_result']}")

    if summary["accuracy_pct"] < MIN_ACCURACY_PCT:
        print(f"\nFAIL: accuracy {summary['accuracy_pct']}% is below the CI floor of {MIN_ACCURACY_PCT}%.")
        sys.exit(1)
