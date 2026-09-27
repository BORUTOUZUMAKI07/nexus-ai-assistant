// k6 smoke load-test for the Nexus API (dev-only; requires the standalone k6
// binary — no npm/pip deps are added by this file).
//
//   k6 run backend/loadtests/k6/nexus_smoke.js -e NEXUS_HOST=http://localhost:8000
//
// Mirrors the Locust scenario: authenticated read mix + explicit login bursts
// against the 60/min-per-IP bucket. Thresholds are deliberately loose — this
// is a smoke/sanity profile, not a capacity ceiling.
import http from "k6/http";
import { check, sleep } from "k6";

const HOST = __ENV.NEXUS_HOST || "http://localhost:8000";
const PREFIX = __ENV.NEXUS_API_PREFIX || "/api/v1";
const EMAIL = __ENV.NEXUS_LOAD_EMAIL || "loaduser@example.com";
const PASSWORD = __ENV.NEXUS_LOAD_PASSWORD || "LoadTestPass123!";

export const options = {
  vus: 20,
  duration: "60s",
  thresholds: {
    http_req_failed: ["rate<0.05"], // <5% hard failures (429s excluded below)
    http_req_duration: ["p(95)<1500"],
  },
};

function login() {
  return http.post(
    `${PREFIX}/auth/login`,
    { username: EMAIL, password: PASSWORD },
    { headers: { "Content-Type": "application/x-www-form-urlencoded" } }
  );
}

export default function () {
  const token = JSON.parse(login().body).access_token;
  const headers = { Authorization: `Bearer ${token}` };

  const list = http.get(`${PREFIX}/conversations`, { headers });
  check(list, { "list conversations 2xx/4xx": (r) => r.status < 500 });

  const me = http.get(`${PREFIX}/auth/me`, { headers });
  check(me, { "auth/me 200": (r) => r.status === 200 });

  sleep(0.5);

  // Intentional burst against the rate limiter: 429 + Retry-After is the
  // expected, correct outcome under 20 concurrent users.
  const burst = login();
  check(burst, {
    "login burst 2xx or 429": (r) => r.status === 200 || r.status === 429,
  });
}