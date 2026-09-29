"""
Unit tests for the LLM-era drift monitor (sliding-window principles).
"""
from backend.app.services.monitoring.drift_monitor import (
    distribution_drift_report,
    psi,
    rate_drift_report,
    sliding_window_pairs,
    summarize_drift,
    window_stats,
)


def test_window_stats_basic():
    stats = window_stats([1.0, 2.0, 3.0])
    assert stats["mean"] == 2.0
    assert stats["count"] == 3
    assert stats["std"] > 0
    assert window_stats([])["count"] == 0


def test_rate_drift_detected_when_recent_way_up():
    baseline = [1.0] * 20
    recent = [5.0] * 20
    report = rate_drift_report(recent, baseline, threshold=2.0)
    assert report["drifting"] is True
    assert report["direction"] == "up"
    assert report["zscore"] > 2.0


def test_rate_drift_in_spec_when_close():
    baseline = [50.0, 51.0, 49.0, 50.0]
    recent = [51.0, 50.0, 50.5]
    report = rate_drift_report(recent, baseline, threshold=2.0)
    assert report["drifting"] is False


def test_insufficient_data_never_flags():
    report = rate_drift_report([1.0, 2.0], [1.0] * 20, threshold=2.0, min_samples=5)
    assert report["drifting"] is False
    assert report["detail"] == "insufficient data"


def test_psi_identical_distributions_zero():
    dist = {"a": 10, "b": 5, "c": 5}
    assert psi(dist, dist) < 1e-6


def test_psi_shifted_distribution_flags():
    expected = {"a": 90, "b": 5, "c": 5}
    observed = {"a": 10, "b": 45, "c": 45}
    report = distribution_drift_report(expected, observed)
    assert report["drifting"] is True
    assert report["psi"] > 0.1


def test_sliding_window_pairs_split():
    samples = [
        {"ts": 10.0, "value": 1.0},
        {"ts": 12.0, "value": 2.0},
        {"ts": 25.0, "value": 3.0},
        {"ts": 30.0, "value": 4.0},
    ]
    recent, baseline = sliding_window_pairs(samples, recent_seconds=10, baseline_seconds=25, now_ts=35.0)
    assert recent == [3.0, 4.0]
    assert baseline == [1.0, 2.0]


def test_summarize_drift_aggregates_flags():
    recent = {"latency_ms": [500.0] * 20}
    baseline = {"latency_ms": [50.0] * 20}
    summary = summarize_drift(recent, baseline, threshold=2.0)
    assert summary["detected"] is True
    assert summary["drifting_metrics"] == 1
    assert summary["metrics"]["latency_ms"]["drifting"] is True
