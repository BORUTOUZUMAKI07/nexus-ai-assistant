"""
Locust load test for Nexus AI Assistant.

Run with:
    locust -f scripts/loadtest/locustfile.py --host http://localhost:8000

The CI gate (scripts/loadtest/run_gate.py) runs Locust headlessly and fails
if P95 latency > 2s or error rate > 1%.

Set LOADTEST_TOKEN in the environment to exercise the authenticated routes.
The token is read at runtime and never printed or stored by the harness.
"""

from __future__ import annotations

import os

from locust import HttpUser, between, task
from targets import (
    AUTH_ME_PATH,
    CONVERSATIONS_PATH,
    HEALTH_PATH,
    auth_headers,
    chat_path,
)


class NexusUser(HttpUser):
    wait_time = between(1, 5)
    host = "http://localhost:8000"

    def on_start(self) -> None:
        token = os.environ.get("LOADTEST_TOKEN", "")
        if token:
            self.client.headers.update(auth_headers(token))
        response = self.client.post(
            CONVERSATIONS_PATH,
            json={"title": "nexus-loadtest"},
            name="create_conversation",
        )
        self.conversation_id = response.json().get("id") if response.status_code == 201 else None

    @task(3)
    def health(self) -> None:
        self.client.get(HEALTH_PATH, name="health")

    @task(1)
    def chat_stream(self) -> None:
        if not self.conversation_id:
            return
        self.client.post(
            chat_path(self.conversation_id),
            json={"content": "Hello"},
            name="chat_stream",
        )

    @task(1)
    def auth_me(self) -> None:
        self.client.get(AUTH_ME_PATH, name="auth_me")
