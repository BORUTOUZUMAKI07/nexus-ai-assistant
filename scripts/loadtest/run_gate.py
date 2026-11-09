"""
Load test gate for CI.

Runs Locust headlessly and fails if P95 latency > 2s or error rate > 1%.

Usage:
    python scripts/loadtest/run_gate.py --host http://localhost:8000 --users 10 --spawn-rate 2 --run-time 60s

Exits non-zero if SLOs are violated, suitable for a CI job gate.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

LOCUSTFILE = Path(__file__).parent / "locustfile.py"


def run_gate(
    host: str,
    users: int,
    spawn_rate: int,
    run_time: str,
    headless: bool = True,
) -> int:
    cmd = [
        "locust",
        "-f",
        str(LOCUSTFILE),
        "--host",
        host,
        "--users",
        str(users),
        "--spawn-rate",
        str(spawn_rate),
        "--run-time",
        run_time,
        "--headless",
        "--only-summary",
        "--csv",
        "/tmp/locust_results",
    ]
    print(f"[locust-gate] Running: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)

    # Parse stats CSV
    stats_path = Path("/tmp/locust_results_stats.csv")
    if not stats_path.exists():
        print("[locust-gate] No stats CSV produced; assuming failure")
        return 1

    import csv

    with open(stats_path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    total_row = next((r for r in rows if r["Name"] == "Aggregated"), None)
    if not total_row:
        print("[locust-gate] No aggregated row in stats; assuming failure")
        return 1

    p95_ms = float(total_row.get("95%", "0") or 0)
    fail_ratio = float(total_row.get("Failure Ratio", "0") or 0)
    total_requests = int(total_row.get("Request Count", "0") or 0)

    print(f"[locust-gate] Requests: {total_requests}")
    print(f"[locust-gate] P95 latency: {p95_ms:.1f}ms")
    print(f"[locust-gate] Error rate: {fail_ratio * 100:.2f}%")

    ok = True
    if p95_ms > 2000:
        print(
            f"[locust-gate] FAIL: P95 {p95_ms:.1f}ms > 2000ms SLO",
            file=sys.stderr,
        )
        ok = False
    if fail_ratio > 0.01:
        print(
            f"[locust-gate] FAIL: error rate {fail_ratio * 100:.2f}% > 1% SLO",
            file=sys.stderr,
        )
        ok = False
    if total_requests == 0:
        print("[locust-gate] FAIL: no requests completed", file=sys.stderr)
        ok = False

    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Locust load test gate")
    parser.add_argument("--host", required=True, help="Target host URL")
    parser.add_argument("--users", type=int, default=10, help="Number of users")
    parser.add_argument("--spawn-rate", type=int, default=2, help="Spawn rate")
    parser.add_argument(
        "--run-time", default="60s", help="Run time (e.g. 60s, 2m)"
    )
    args = parser.parse_args()
    return run_gate(args.host, args.users, args.spawn_rate, args.run_time)


if __name__ == "__main__":
    sys.exit(main())
