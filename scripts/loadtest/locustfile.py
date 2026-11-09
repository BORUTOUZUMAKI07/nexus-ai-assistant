"""
Locust load test for Nexus AI Assistant.

Run with:
    locust -f scripts/loadtest/locustfile.py --host http://localhost:8000

The CI gate (scripts/loadtest/run_gate.py) runs Locust headlessly and fails
if P95 latency > 2s or error rate > 1%.
"""
from locust import HttpUser, task, between


class NexusUser(HttpUser):
    wait_time = between(1, 5)
    host = "http://localhost:8000"

    @task(3)
    def health(self):
        self.client.get("/health", name="health")

    @task(1)
    def chat_stream(self):
        self.client.post(
            "/api/v1/chat",
            json={"message": "Hello", "model": "fast_chat", "conversation_id": None},
            name="chat_stream",
        )

    @task(1)
    def auth_me(self):
        self.client.get("/api/v1/auth/me", name="auth_me")
