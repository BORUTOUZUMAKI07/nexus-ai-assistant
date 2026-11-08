#!/usr/bin/env python3
"""Tripwire: refuse to commit tracked files that look like secrets."""
from __future__ import annotations
import re, subprocess, sys
from pathlib import Path

PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key ID"),
    (re.compile(r"ghp_[0-9a-zA-Z]{36}"), "GitHub Personal Access Token"),
    (re.compile(r"github_pat_[0-9a-zA-Z_]{82}"), "GitHub Fine-Grained PAT"),
    (re.compile(r"-----BEGIN (RSA|DSA|EC|OPENSSH) PRIVATE KEY-----"), "Private key"),
    (re.compile(r"sk-[a-zA-Z0-9]{48}"), "OpenAI API Key"),
]
ALLOWLIST_PATHS = {"backend/tests/", "frontend/src/test/", ".github/scripts/check_secrets.py", "docs/"}
FAKE_PATTERNS = ["test-secret-key", "test_key", "example", "dummy", "fake", "xxx", "changeme"]

def scan() -> int:
    try:
        result = subprocess.run(["git", "ls-files"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, text=True)
    except Exception as e:
        print(f"Failed: {e}", file=sys.stderr)
        return 1
    violations = []
    for path in result.stdout.splitlines():
        skip = any(path.startswith(a) for a in ALLOWLIST_PATHS)
        if skip or not Path(path).is_file():
            continue
        try:
            content = Path(path).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if any(f in content.lower() for f in FAKE_PATTERNS):
            continue
        for pattern, name in PATTERNS:
            if pattern.search(content):
                violations.append(f"{path}: {name}")
                break
    if violations:
        print("SECRET SCAN FAILED", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    print("Secret scan passed")
    return 0

if __name__ == "__main__":
    sys.exit(scan())
