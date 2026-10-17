"""Guards for `docker/otel-collector-config.yaml`: the collector must build it.

On 2026-10-07 the collector refused to start against its own tracked config:

    Error: failed to build pipelines: failed to create "resource" processor,
    in pipeline "logs": error creating AttrProc due to unsupported action ""
    at the 0-th actions

exit 1, before /healthz ever answered — and nothing observed it. The symptom
from outside was an empty New Relic Logs tab, which is indistinguishable from
an app that logged nothing (AGENTS.md §9.26: configured-but-broken).

`otelcol validate` does NOT catch this. Measured on
otel/opentelemetry-collector-contrib:0.121.0, three variants of this file:

    variant                      exit
    FIXED (action declared)      0
    STRIPPED (action removed)    0   <- the config that dies at startup
    GARBAGE top-level key        1

so `validate` only unmarshals — it never builds the component graph, which is
where the rejection happens, and the GARBAGE row is what proves the file
really was read. A guard built on `validate` would have passed against the
precise defect it was written for.

Four tests, deliberately different in strength:

* `test_every_resource_attribute_declares_an_action` — static, needs no
  Docker, and names the missing field. It still guards a machine with no
  daemon at all.
* `test_collector_hardening_is_wired` — static: pins the features that are
  invisible when reverted (basicauth on the public receiver, the file-backed
  durable sending queue, `service.version`, gzip on the exporter, and metrics
  over OTLP rather than a Prometheus scrape).
* `test_tail_sampling_keeps_errors_and_a_sample_of_the_rest` — static: the
  quota guard. `UNSET` is a healthy span's default status, so listing it keeps
  nearly every trace and `sample-rest` then drops nothing, shipping the whole
  trace stream to the free-tier sink silently.
* `test_the_collector_really_builds_the_tracked_config` — starts the pinned
  image on the tracked file and waits for readiness. This asks the collector
  itself instead of asserting the shape a repo test claims it wants
  (AGENTS.md §9.15: test the integration, not the object), so it catches any
  future build-time rejection — not just this one field.

Neither proves telemetry reaches New Relic. That needs a live ingest key and
network, which is a deployment step (`docs/observability-deployment.md` §5),
not something a test should depend on.
"""
from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_CONFIG = REPO_ROOT / "docker" / "otel-collector-config.yaml"

# The tag docker-compose.yml mounts, so the image and the config file move
# together or not at all.
IMAGE = "otel/opentelemetry-collector-contrib:0.121.0"

# The only values the resource processor's `action` field accepts.
ALLOWED_ACTIONS = frozenset({"insert", "update", "upsert", "delete"})

# Readiness lands ~0.8s after start (measured); a rejected config exits
# immediately. Polling for the marker rather than sleeping a fixed period
# keeps a healthy run quick, a broken one fast, and bounds both.
STARTUP_TIMEOUT_SECONDS = 20.0
POLL_INTERVAL_SECONDS = 0.5
READY_MARKER = "Everything is ready"


def _docker(args: list[str], timeout: float = 60) -> subprocess.CompletedProcess[str]:
    """Run a docker subcommand, capturing both streams as text.

    `encoding`/`errors` match the rest of the suite: on Windows the child's
    output is not reliably UTF-8, and a decode error here would look like a
    Docker failure.
    """
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _require_docker() -> None:
    """Skip — never fail — when Docker itself is unavailable.

    The suite already treats a missing daemon as environment rather than
    regression (AGENTS.md §7: ~113 setup errors when Docker Desktop is off).
    This test is about a config file; going red because the daemon is down
    would teach people to ignore the signal that matters.
    """
    if shutil.which("docker") is None:
        pytest.skip("docker is not on PATH; the collector startup check needs it")

    try:
        probe = _docker(["info"], timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"docker is not runnable: {type(exc).__name__}: {exc}")
    if probe.returncode != 0:
        pytest.skip("docker daemon is not running (environment, not a regression)")

    # The image is 400MB; only pay for it if it is genuinely missing, and skip
    # (with the reason) rather than fail when the network cannot supply it —
    # "no image" is not "the config is broken".
    if _docker(["image", "inspect", IMAGE], timeout=60).returncode == 0:
        return
    try:
        pull = _docker(["pull", IMAGE], timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"cannot pull {IMAGE}: {type(exc).__name__}: {exc}")
    if pull.returncode != 0:
        pytest.skip(f"cannot pull {IMAGE}: {pull.stderr.strip()[:300]}")


def _wait_until_ready(name: str) -> str | None:
    """Poll the container; `None` means readiness, else a failure reason.

    Readiness is the property under test, not liveness: the collector prints
    the marker only *after* every pipeline has been built, which is the step
    that rejected this config.
    """
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        state = _docker(
            ["inspect", name, "--format", "{{.State.Running}}|{{.State.ExitCode}}"],
            timeout=30,
        )
        running, _, exit_code = state.stdout.strip().partition("|")
        if running != "true":
            return f"the collector exited with code {exit_code} instead of building the config"

        logs = _docker(["logs", name], timeout=30)
        if READY_MARKER in logs.stdout + logs.stderr:
            return None
        time.sleep(POLL_INTERVAL_SECONDS)

    return f"no readiness line after {STARTUP_TIMEOUT_SECONDS:.0f}s"


def test_every_resource_attribute_declares_an_action() -> None:
    """The static half: name the field, with no Docker involved.

    Narrow by design — it only knows about one schema requirement, and would
    not notice a different build-time error. The startup test below is what
    covers the class; this one exists so the failure message says *which*
    entry is wrong instead of "collector did not become ready".
    """
    config = yaml.safe_load(COLLECTOR_CONFIG.read_text(encoding="utf-8"))
    resource = (config.get("processors") or {}).get("resource")
    assert resource, (
        "processors.resource is missing; every pipeline in "
        "docker/otel-collector-config.yaml lists it, so the collector would not start"
    )

    attributes = resource.get("attributes") or []
    # An empty list would let the loop below pass without asserting anything
    # (AGENTS.md §7: a loop over a possibly-empty collection is not a test).
    assert attributes, "processors.resource.attributes is empty; nothing would be checked"

    for index, entry in enumerate(attributes):
        assert "action" in entry, (
            f"processors.resource.attributes[{index}] (key={entry.get('key')!r}) omits "
            "`action`. collector-contrib v0.121.0 then builds an empty action and "
            'refuses to start: failed to create "resource" processor ... '
            'unsupported action "" — the container exits 1 before /healthz, and the '
            "only symptom is telemetry that never arrives."
        )
        assert entry["action"] in ALLOWED_ACTIONS, (
            f"processors.resource.attributes[{index}] has action={entry['action']!r}; "
            f"one of {sorted(ALLOWED_ACTIONS)} is required"
        )


def test_collector_hardening_is_wired() -> None:
    """The features that are invisible when reverted are pinned structurally.

    A dropped durable queue still forwards telemetry while the sink is up; a
    removed auth block still starts the collector. Neither produces a red test
    at runtime, so the shape has to be asserted here (AGENTS.md §2). Static, so
    it names the missing piece with no Docker involved.
    """
    config = yaml.safe_load(COLLECTOR_CONFIG.read_text(encoding="utf-8"))
    extensions = config.get("extensions") or {}
    service_extensions = config.get("service", {}).get("extensions") or []

    # 1. The public OTLP receiver is behind basic auth.
    assert "basicauth/server" in extensions, (
        "extensions.basicauth/server is missing; the OTLP receiver is a public "
        "endpoint on Render and would accept unauthenticated writes"
    )
    http_protocol = (
        ((config.get("receivers") or {}).get("otlp") or {})
        .get("protocols", {})
        .get("http", {})
    )
    assert (http_protocol.get("auth") or {}).get("authenticator") == "basicauth/server", (
        "receivers.otlp.protocols.http.auth.authenticator must name "
        "basicauth/server, or the extension above enforces nothing"
    )
    assert "basicauth/server" in service_extensions, (
        "basicauth/server must be listed in service.extensions to be loaded"
    )

    # 2. The New Relic exporter has a durable, file-backed sending queue.
    newrelic = (config.get("exporters") or {}).get("otlphttp/newrelic") or {}
    queue = newrelic.get("sending_queue") or {}
    assert queue.get("enabled") is True, (
        "exporters.otlphttp/newrelic.sending_queue.enabled must be true so a "
        "New Relic outage buffers instead of dropping telemetry"
    )
    assert queue.get("storage") == "file_storage", (
        "the sending queue must be backed by file_storage to survive a restart"
    )
    assert "file_storage" in extensions and "file_storage" in service_extensions, (
        "file_storage must be declared in extensions and loaded in service.extensions"
    )

    # 3. service.version stamps every signal, so a release is identifiable.
    attributes = ((config.get("processors") or {}).get("resource") or {}).get("attributes") or []
    keys = {entry.get("key") for entry in attributes}
    assert "service.version" in keys, (
        "processors.resource.attributes must set service.version"
    )

    # 4. Compress the payload. Free-tier egress is metered, and an uncompressed
    # OTLP body is several times larger for identical spans; dropping this is
    # invisible while the sink still accepts the uncompressed stream.
    assert newrelic.get("compression") == "gzip", (
        "exporters.otlphttp/newrelic.compression must be gzip — otherwise the "
        "same telemetry costs several times the free-tier bandwidth"
    )

    # 5. Metrics arrive over OTLP, not a Prometheus scrape. This pins the
    # deliberate migration: reverting to the prometheus receiver silently
    # restores the per-worker undercount and the public scrape target.
    metrics = config.get("service", {}).get("pipelines", {}).get("metrics") or {}
    assert "otlp" in (metrics.get("receivers") or []), (
        "the metrics pipeline must use the otlp receiver"
    )
    assert "prometheus" not in (metrics.get("receivers") or []), (
        "the metrics pipeline must not use the prometheus receiver"
    )


def test_tail_sampling_keeps_errors_and_a_sample_of_the_rest() -> None:
    """The quota guard: the error policy must NOT match every healthy span.

    ``status_code`` accepts ``OK``/``ERROR``/``UNSET``, and ``UNSET`` is the
    default status of a span that simply succeeded. Listing it makes the policy
    match nearly every trace, so the collector keeps them all and
    ``sample-rest`` never drops anything — the ENTIRE trace stream ships to the
    (free-tier) sink, silently. Naming only ``ERROR`` is what makes "errors +
    ~10% of the rest" true, and the latency policy keeps the slow traces a
    healthy status would otherwise hide. Static, so it runs with no daemon.
    """
    config = yaml.safe_load(COLLECTOR_CONFIG.read_text(encoding="utf-8"))
    policies = {
        policy["name"]: policy
        for policy in config["processors"]["tail_sampling"]["policies"]
    }

    errors = policies["keep-all-errors"]
    assert errors["type"] == "status_code"
    assert errors["status_code"]["status_codes"] == ["ERROR"], (
        "the status_code policy must list only ERROR: UNSET is the default "
        "status of a healthy span, so [ERROR, UNSET] matches almost every "
        "trace and silently disables the probabilistic sampling of the rest"
    )

    slow = policies["keep-slow"]
    assert slow["type"] == "latency"
    assert slow["latency"]["threshold_ms"] == 2000, (
        "the latency policy must keep traces slower than 2s — a healthy status "
        "would otherwise let a slow-but-successful trace be dropped"
    )

    rest = policies["sample-rest"]
    assert rest["type"] == "probabilistic"
    assert rest["probabilistic"]["sampling_percentage"] == 10, (
        "sample-rest is the fraction that actually reaches the free-tier sink"
    )

    # Tail sampling buffers whole traces, so it belongs on the traces pipeline
    # only: logs and metrics have no trace to sample, and attaching it there is
    # a configuration error.
    pipelines = config["service"]["pipelines"]
    assert "tail_sampling" in pipelines["traces"]["processors"]
    for name in ("logs", "metrics"):
        assert "tail_sampling" not in pipelines[name]["processors"], (
            f"{name} has no trace to sample; attaching tail_sampling there is a "
            "configuration error"
        )


def test_the_collector_really_builds_the_tracked_config() -> None:
    """The integration half: the collector itself accepts the tracked config.

    Replaces `otelcol validate`, which was measured to pass on the broken
    config (module docstring) — asking a validator that never builds the
    graph would be testing the object, not the integration.
    """
    _require_docker()

    name = f"nexus-cfg-check-{uuid.uuid4().hex[:8]}"
    started = _docker(
        [
            "run",
            "-d",
            "--name",
            name,
            "-v",
            f"{COLLECTOR_CONFIG}:/etc/otelcol-contrib/config.yaml",
            # file_storage's directory must exist and be writable by the
            # collector's non-root uid (10001). The distroless image has no
            # writable /tmp, so mount a world-writable tmpfs at the tracked
            # path — the same ownership a fresh docker named volume inherits
            # from docker/otel-collector.Dockerfile in a real deployment.
            "--tmpfs",
            "/var/lib/otelcol/storage:rw,mode=1777",
            # The config reads these via ${env:...}. An unset *name* is an
            # error for the collector, so they are supplied exactly as
            # docker-compose.yml supplies them.
            "-e",
            "ENVIRONMENT=development",
            "-e",
            "SERVICE_VERSION=dev",
            "-e",
            "OTLP_AUTH_USER=nexus",
            "-e",
            "OTLP_AUTH_PASSWORD=nexus",
            "-e",
            "NEW_RELIC_LICENSE_KEY=not-set",
            IMAGE,
            "--config=/etc/otelcol-contrib/config.yaml",
        ],
        timeout=120,
    )
    assert started.returncode == 0, f"docker run failed:\n{started.stderr.strip()}"

    try:
        failure = _wait_until_ready(name)
        logs = "" if failure is None else _tail(name)
    finally:
        # Always remove: a leaked container would collide with nothing (the
        # name is unique) but would keep a rejected config alive to confuse
        # whoever looks at `docker ps` next.
        _docker(["rm", "-f", name], timeout=60)

    assert failure is None, (
        f"{failure} — docker/otel-collector-config.yaml must build its pipeline "
        f'graph (readiness marker: "{READY_MARKER}").\n'
        f"--- collector log (tail) ---\n{logs}"
    )


def _tail(name: str, lines: int = 25) -> str:
    """Last `lines` of the container's output, for the failure message."""
    logs = _docker(["logs", name], timeout=30)
    combined = (logs.stdout + logs.stderr).splitlines()
    return "\n".join(combined[-lines:])
