"""
Locust load test for the Nexus API (dev-only tooling; not collected by pytest).

Run against a local/dev instance:

    locust -f backend/loadtests/locustfile.py --host http://localhost:8000

or in headless mode for a quick regression soak:

    locust -f backend/loadtests/locustfile.py --host http://localhost:8000 \\
        --headless -u 20 -r 2 -t 60s

The scenario deliberately mixes two concerns:

  * a steady authenticated read/write mix (conversations.list, auth.me,
    conversations.create) that mirrors normal dashboard traffic, and
  * a ``login_burst`` task that hammers POST /auth/login — the 60 req/min
    per-IP bucket — so sustained load visibly triggers the RFC 6585 429 +
    Retry-After + X-RateLimit-* headers without making the rest of the
    scenario flaky (429s on login are an *expected* outcome under load).

Credentials come from ``NEXUS_LOAD_EMAIL`` / ``NEXUS_LOAD_PASSWORD`` env vars
so the script never embeds secrets; ``NEXUS_API_PREFIX`` overrides the API
mount when the app runs under a different prefix.
"""
import os

from locust import HttpUser, between, task

API_V1_PREFIX = os.environ.get("NEXUS_API_PREFIX", "/api/v1")
LOAD_EMAIL = os.environ.get("NEXUS_LOAD_EMAIL", "loaduser@example.com")
LOAD_PASSWORD = os.environ.get("NEXUS_LOAD_PASSWORD", "LoadTestPass123!")


class NexusUser(HttpUser):
    """Typical API consumer: logs in once, then reads/writes with the token."""

    wait_time = between(0.4, 1.2)
    token: str | None = None

    def on_start(self) -> None:
        resp = self.client.post(
            f"{API_V1_PREFIX}/auth/login",
            data={"username": LOAD_EMAIL, "password": LOAD_PASSWORD},
            name="auth.login",
        )
        if resp.status_code == 200:
            self.token = (resp.json() or {}).get("access_token")

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    @task(3)
    def list_conversations(self) -> None:
        self.client.get(
            f"{API_V1_PREFIX}/conversations",
            headers=self._auth_headers(),
            name="conversations.list",
        )

    @task(2)
    def get_me(self) -> None:
        self.client.get(
            f"{API_V1_PREFIX}/auth/me",
            headers=self._auth_headers(),
            name="auth.me",
        )

    @task(1)
    def create_conversation(self) -> None:
        self.client.post(
            f"{API_V1_PREFIX}/conversations",
            json={"title": "load test"},
            headers=self._auth_headers(),
            name="conversations.create",
        )

    @task(1)
    def login_burst(self) -> None:
        # Exercises the 60/min per-IP bucket on the login route: under real
        # load this task starts returning 429 with Retry-After together with
        # the X-RateLimit-* headers — verify those in the Locust response tab.
        self.client.post(
            f"{API_V1_PREFIX}/auth/login",
            data={"username": LOAD_EMAIL, "password": LOAD_PASSWORD},
            name="auth.login",
        )