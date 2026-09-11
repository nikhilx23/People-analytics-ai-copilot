"""Guards against silent regressions in the copilot pipeline: the evaluation
harness must maintain a minimum accuracy against the checked-in eval dataset."""

from app.evaluation.eval_runner import run_eval

MIN_ACCEPTABLE_ACCURACY = 98.0  # matches eval_runner.MIN_ACCURACY_PCT - see that module's comment


def test_evaluation_accuracy_meets_minimum_bar():
    summary = run_eval()
    failures = [r for r in summary["results"] if not r["correct"]]
    assert summary["accuracy_pct"] >= MIN_ACCEPTABLE_ACCURACY, f"Failures: {failures}"
