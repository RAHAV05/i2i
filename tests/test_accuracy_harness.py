"""Unit tests for Phase 0 Accuracy Scoring Harness (tools/accuracy_harness.py)."""

import pytest
from tools.accuracy_harness import (
    levenshtein_distance,
    compute_cer,
    AccuracyScoringHarness,
    generate_reference_benchmark_cases
)


def test_levenshtein_distance():
    assert levenshtein_distance("", "") == 0
    assert levenshtein_distance("abc", "abc") == 0
    assert levenshtein_distance("kitten", "sitting") == 3
    assert levenshtein_distance("120/80", "120/80") == 0
    assert levenshtein_distance("120/80", "130/80") == 1


def test_compute_cer():
    assert compute_cer("hello", "hello") == 0.0
    assert compute_cer("MD-1234", "MD-1235") == pytest.approx(1 / 7, 0.01)
    assert compute_cer("", "") == 0.0
    assert compute_cer("abc", "") == 1.0


def test_accuracy_harness_benchmark():
    gt_cases, hyp_cases = generate_reference_benchmark_cases(10)
    harness = AccuracyScoringHarness(gt_cases)
    report = harness.run_benchmark(hyp_cases)

    assert report["total_cases_evaluated"] == 10
    assert report["metrics"]["id_accuracy"] >= 0.99
    assert report["metrics"]["vitals_accuracy"] >= 0.98
    assert report["metrics"]["silent_critical_error_count"] == 0
    assert report["safety_gates"]["all_gates_passed"] is True


def test_silent_critical_error_flagged():
    """Verify that if the model predicts wrong BP with high confidence, it is flagged as a Silent Critical Error."""
    gt = {
        "case_id": "CRIT-001",
        "fields": {
            "blood_pressure": {"value": "180/110", "is_unclear": False}
        }
    }
    # Model hallucinated normal BP with 0.95 confidence
    hyp = {
        "case_id": "CRIT-001",
        "fields": {
            "blood_pressure": {"raw_text": "120/80", "normalized_value": "120/80", "confidence": 0.95, "status": "valid"}
        }
    }

    harness = AccuracyScoringHarness([gt])
    report = harness.run_benchmark([hyp])

    assert report["metrics"]["silent_critical_error_count"] == 1
    assert report["safety_gates"]["silent_critical_error_pass"] is False
    assert report["safety_gates"]["all_gates_passed"] is False
    assert report["silent_critical_errors"][0]["field"] == "blood_pressure"
