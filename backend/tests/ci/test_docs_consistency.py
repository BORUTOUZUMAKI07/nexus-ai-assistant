"""Documentation must not contradict the shipped manifest or deployment target.

Two drifts were found by the audit: prose still claimed Next.js 16.4.2 while the
manifest declares 16.4.6, and one section called the Docker image the
"recommended deployment" even though production runs the frontend on Vercel.
"""
import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def test_documented_nextjs_version_matches_the_manifest():
    manifest = json.loads((_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    declared = manifest["dependencies"]["next"].lstrip("^")
    lock = json.loads((_ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    locked = lock["packages"][""]["dependencies"]["next"].lstrip("^")
    assert declared == locked, "manifest and lockfile disagree about the Next.js version"

    for relative in ("docs/deployment-guide.md", "docs/user-guide.md", "frontend/README.md"):
        path = _ROOT / relative
        stale = set(re.findall(r"16\.4\.\d+", path.read_text(encoding="utf-8"))) - {declared}
        assert not stale, f"{relative} still claims {sorted(stale)}; manifest says {declared}"


def test_deployment_guide_names_vercel_as_the_reference_frontend_target():
    """RED: the frontend section must not recommend the self-host image instead."""
    text = (_ROOT / "docs" / "deployment-guide.md").read_text(encoding="utf-8")
    section = text.split("## 5. Frontend", 1)[1].split("## 6.", 1)[0]

    assert "Vercel" in section
    assert "BACKEND_URL" in section
    assert "recommended deployment is the provided `frontend/Dockerfile`" not in section


def test_topology_table_still_places_the_frontend_on_vercel():
    text = (_ROOT / "docs" / "deployment-guide.md").read_text(encoding="utf-8")
    assert "| Frontend | **Vercel** |" in text
    assert (_ROOT / "frontend" / "vercel.json").is_file()
