"""
Drift Monitoring for an LLM Assistant.

Adapts the ML-systems drift toolkit (MD §7.6 — sliding vs cumulative windows,
z-score alerting, PSI over categorical distributions) to *LLM-era* signals
(MD §8.8 monitoring row): refusal rate, hallucination/faithfulness pass rate,
error rate, latency, and cost per request.

Design rules from MD §7.6 applied here:
* **Sliding windows** — never cumulative, which hides recent dips under old
  history. Compare a recent window against the immediately preceding baseline
  window of equal length.
* **z-score alerting** — a metric is flagged when its current window rate is
  > ``threshold`` standard deviations from the baseline mean.
* **PSI for distributions** — categorical histograms (when available) drift
  when the population stability index exceeds the classic 0.1 threshold.

All functions are pure/deterministic so they are unit-testable without a DB.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

_WARN_PSI = 0.1  # classic PSI warning band


def _mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def window_stats(values: Sequence[float]) -> dict[str, float | int]:
    """Mean/std/count for a window. Std of a single sample is 0 (no drift signal)."""
    vals = [float(v) for v in values if v is not None]
    return {"mean": _mean(vals), "std": _std(vals), "count": len(vals)}


def rate_drift_report(
    recent_values: Sequence[float],
    baseline_values: Sequence[float],
    threshold: float = 2.0,
    min_samples: int = 5,
) -> dict[str, Any]:
    """
    Compare a recent sliding window against the immediately preceding one.

    A metric drifts when z = (recent_mean - baseline_mean) / std > threshold.
    Conservative gates:
    * fewer than ``min_samples`` in either window → no alert (insufficient data);
    * baseline std == 0 and means differ beyond a small epsilon → alert only
      when the *change ratio* is material (avoids alerting on noise).
    """
    recent = [float(v) for v in recent_values if v is not None]
    baseline = [float(v) for v in baseline_values if v is not None]
    report: dict[str, Any] = {
        "recent_mean": _mean(recent),
        "baseline_mean": _mean(baseline),
        "samples_recent": len(recent),
        "samples_baseline": len(baseline),
        "drifting": False,
        "zscore": 0.0,
        "direction": "stable",
        "detail": "insufficient data",
    }
    if len(recent) < min_samples or len(baseline) < min_samples:
        return report

    b_mean = _mean(baseline)
    b_std = _std(baseline)
    r_mean = _mean(recent)

    if b_std > 1e-9:
        z = (r_mean - b_mean) / b_std
        report["zscore"] = round(z, 3)
        report["drifting"] = abs(z) > threshold
        report["direction"] = "up" if z > 0 else ("down" if z < 0 else "stable")
        report["detail"] = "z-score exceeds threshold" if abs(z) > threshold else "within baseline tolerance"
        return report

    # Zero-variance baseline: only a materially large relative shift counts.
    if abs(r_mean - b_mean) <= 1e-9:
        report["detail"] = "identical to baseline"
        return report
    ratio = abs(r_mean - b_mean) / max(abs(b_mean), 1e-9)
    report["zscore"] = float("inf") if ratio > threshold else 0.0
    report["drifting"] = ratio > threshold
    report["direction"] = "up" if r_mean > b_mean else "down"
    report["detail"] = "baseline had zero variance; flagged on relative shift" if report["drifting"] else "small absolute shift"
    return report


def psi(
    expected_counts: Mapping[str, float],
    observed_counts: Mapping[str, float],
    eps: float = 1e-9,
) -> float:
    """
    Population Stability Index over two categorical distributions.

    psi = Σ (obs_p - exp_p) * ln(obs_p / exp_p). Values below 0.1 indicate no
    meaningful shift, 0.1–0.25 a moderate shift, above 0.25 a major shift.
    Both sides are normalized to probabilities and zero counts are floored at
    ``eps`` so the log term never diverges.
    """
    keys = set(expected_counts) | set(observed_counts)
    exp_total = sum(expected_counts.values()) or 1.0
    obs_total = sum(observed_counts.values()) or 1.0
    score = 0.0
    for key in keys:
        exp_p = (expected_counts.get(key, 0.0) or 0.0) / exp_total
        obs_p = (observed_counts.get(key, 0.0) or 0.0) / obs_total
        exp_p = max(exp_p, eps)
        obs_p = max(obs_p, eps)
        score += (obs_p - exp_p) * math.log(obs_p / exp_p)
    return round(score, 6)


def distribution_drift_report(
    expected_counts: Mapping[str, float],
    observed_counts: Mapping[str, float],
    warn_threshold: float = _WARN_PSI,
) -> dict[str, Any]:
    """Flag distribution drift when PSI exceeds the warning band."""
    score = psi(expected_counts, observed_counts)
    return {
        "psi": score,
        "drifting": score > warn_threshold,
        "severity": "major" if score > 0.25 else ("moderate" if score > _WARN_PSI else "none"),
        "detail": "PSI exceeds warning band" if score > warn_threshold else "no meaningful shift",
    }


def sliding_window_pairs(
    samples: Sequence[dict[str, Any]],
    *,
    recent_seconds: float,
    baseline_seconds: float,
    now_ts: float | None = None,
    value_key: str = "value",
    ts_key: str = "ts",
) -> tuple[list[float], list[float]]:
    """
    Split time-point samples into (recent, baseline) equal-length windows.

    ``samples`` are dicts with a timestamp key and a value key; the recent
    window covers the last ``recent_seconds`` up to ``now_ts`` and the baseline
    window the ``baseline_seconds`` immediately before that. Equal-length
    windows keep the comparison fair (MD §7.6).
    """
    if now_ts is None:
        now_ts = max((float(s.get(ts_key, 0.0)) for s in samples), default=0.0)
    recent = [float(s[value_key]) for s in samples if now_ts - float(s.get(ts_key, 0.0)) <= recent_seconds and float(s.get(ts_key, 0.0)) <= now_ts]
    baseline = [
        float(s[value_key])
        for s in samples
        if now_ts - float(s.get(ts_key, 0.0)) > recent_seconds
        and now_ts - float(s.get(ts_key, 0.0)) <= recent_seconds + baseline_seconds
        and float(s.get(ts_key, 0.0)) <= now_ts
    ]
    return recent, baseline


def summarize_drift(
    recent_metrics: Mapping[str, Iterable[float]],
    baseline_metrics: Mapping[str, Iterable[float]],
    threshold: float = 2.0,
) -> dict[str, Any]:
    """
    Produce a single drift summary across many metrics.

    ``recent_metrics``/``baseline_metrics`` map metric name → per-request
    values (0.0/1.0 for rates, ms for latency, $ for cost). Missing keys in
    either side default to an empty window (no signal → not drifting).
    """
    drift_flags = 0
    metrics: dict[str, Any] = {}
    for name in set(recent_metrics) | set(baseline_metrics):
        report = rate_drift_report(
            list(recent_metrics.get(name, [])),
            list(baseline_metrics.get(name, [])),
            threshold=threshold,
        )
        metrics[name] = report
        if report["drifting"]:
            drift_flags += 1
    return {
        "detected": drift_flags > 0,
        "drifting_metrics": drift_flags,
        "metrics": metrics,
    }
