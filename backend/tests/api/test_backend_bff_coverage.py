"""Every backend API route must be reachable from the browser, or be excepted.

This is the seam L4 found missing. The existing frontend ``api-parity`` test
compares BFF routes against MSW mocks, so a backend endpoint with *no BFF route
at all* passes every test and is simply unreachable from the product — exactly
how the 2FA endpoints shipped without a working browser flow (H3).

Here the FastAPI OpenAPI document is the source of truth for what the backend
exposes, and the Next.js ``route.ts`` files are the thing under test. A backend
route counts as covered when either:

  * a BFF route mirrors it under ``/api`` (e.g. ``/auth/2fa/verify``), or
  * a BFF route proxies to it directly (templates, direct ``fetch`` to
    ``/api/v1/...`` for binary/streaming endpoints all count).

Backend routes the browser intentionally does not call are recorded in
``NO_BFF`` with a reason. The list is checked for staleness too, so adding a BFF
route for an excepted endpoint forces its entry to be removed rather than
silently rotting into a blanket allowlist.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BFF_API = REPO_ROOT / "frontend" / "src" / "app" / "api"


# Backend paths the browser does not call. Every entry needs a reason: a silent
# allowlist would recreate the blind spot this test exists to close.
NO_BFF: dict[str, str] = {
    "/admin/audit/eu-act": "EU-Act detail is embedded in the /admin/audit report the Compliance tab renders.",
    "/admin/hooks/reload": "Operational reload endpoint; no browser flow triggers it.",
    "/admin/monitoring/drift": "Monitoring drill-down not wired to an admin tab.",
    "/admin/monitoring/observability": "Monitoring drill-down not wired to an admin tab.",
    "/admin/monitoring/viewer": "Monitoring drill-down not wired to an admin tab.",
    "/admin/monitoring/viewer-data": "Monitoring drill-down not wired to an admin tab.",
    "/admin/users/*": "User deletion is an admin API operation; the UI only toggles active status.",
    "/auth/forgot-password": "No password-recovery page exists in this frontend.",
    "/auth/resend-verification": "No verification UI exists (EMAIL_VERIFICATION_REQUIRED=false).",
    "/auth/reset-password": "No password-recovery page exists in this frontend.",
    "/conversations/*/messages": "Synchronous chat is an API path; the browser uses the streaming path via /api/chat.",
    "/conversations/*/messages/stream": "Alternate streaming entry point; the browser uses /conversations/{id}/stream.",
    "/conversations/search": "Conversation search is served through the list endpoint's query parameters.",
    "/files/*/chunks": "RAG chunk inspection is backend tooling, not a browser flow.",
    "/files/*/index-status": "Index status is backend tooling, not a browser flow.",
    "/orgs/*/members/*": "Member-role mutation is an API operation; the org UI only lists members.",
    "/orgs/*/usage-summary": "Org usage summary is not rendered in the current UI.",
    "/settings/memories/*/history": "Per-memory history API is not exposed in the settings UI.",
    "/tools/approval": "Direct tool approval is an API path; the browser approves through /api/hitl.",
    "/tools/elicitations": "Elicitation management is handled by the agent/API, not the browser.",
    "/tools/elicitations/*/respond": "Elicitation response is handled by the agent/API, not the browser.",
    "/tools/execute": "Direct tool execution is an API path; the browser invokes tools through chat.",
    "/usage/evaluations": "Evaluation usage report is not wired to a UI tab.",
    "/webhooks/*/redeliver/*": "Redelivery is an operator/API action; the UI only lists deliveries.",
}


def _collapse_interpolations(path: str) -> str:
    """Turn JS ``${expr}`` into ``*`` (or nothing for query-string builders)."""
    out: list[str] = []
    i = 0
    while i < len(path):
        if path.startswith("${", i):
            depth = 0
            j = i + 2
            start = j
            while j < len(path):
                if path[j] == "{":
                    depth += 1
                elif path[j] == "}":
                    if depth == 0:
                        break
                    depth -= 1
                j += 1
            inner = path[start:j]
            out.append("" if "?" in inner else "*")
            i = j + 1
        else:
            out.append(path[i])
            i += 1
    return "".join(out)


def _shape(path: str) -> str:
    """Normalise every dynamic-segment style to ``*`` and drop the API prefix."""
    path = path.replace("${BACKEND_URL}", "")
    path = _collapse_interpolations(path)
    path = path.split("?", 1)[0].split("#", 1)[0]
    path = path.replace("/api/v1", "")
    path = re.sub(r"\{[^}]*\}", "*", path)
    path = re.sub(r"\[[^\]]*\]", "*", path)
    path = re.sub(r":[^/]+", "*", path)
    path = re.sub(r"/+", "/", path)
    return path.rstrip("/")


def _backend_shapes() -> set[str]:
    from backend.app.main import app

    return {
        _shape(path)
        for path in app.openapi()["paths"]
        if path.startswith("/api/v1")
    }


def _bff_coverage() -> set[str]:
    """Every backend path a BFF route references, plus each BFF route's own shape."""
    covered: set[str] = set()
    for route in BFF_API.rglob("route.ts"):
        text = route.read_text(encoding="utf-8")
        relative = route.parent.relative_to(BFF_API).as_posix()
        if relative != ".":
            covered.add(_shape("/" + relative))
        for match in re.finditer(
            r'(?:proxyJson|backendFetch)\(\s*[`"\']([^`"\']+)[`"\']', text
        ):
            covered.add(_shape(match.group(1)))
        # Streaming/binary routes call fetch with a `${BACKEND_URL}/api/v1/...`
        # URL built inline, so they have no proxyJson/backendFetch call to find.
        for match in re.finditer(r'[`"\']([^`"\']*/api/v1/[^`"\']*)[`"\']', text):
            covered.add(_shape(match.group(1)))
    return covered


def test_every_backend_route_is_reachable_or_has_a_recorded_exception():
    backend = _backend_shapes()
    covered = _bff_coverage()
    uncovered = sorted(backend - covered - set(NO_BFF))

    assert uncovered == [], (
        "Backend routes with no BFF route and no recorded exception (a browser "
        f"client cannot call these): {uncovered}"
    )


def test_exceptions_are_not_stale():
    """An exception for an endpoint that is now covered must be deleted.

    Otherwise the allowlist grows forever and stops documenting real intent.
    """
    backend = _backend_shapes()
    covered = _bff_coverage()
    stale = sorted(set(NO_BFF) & (covered | (set(NO_BFF) - backend)))

    assert stale == [], (
        "These NO_BFF entries are stale — remove them now that the endpoint is "
        f"covered or no longer exists: {stale}"
    )
