"""The embedding backend is chosen deliberately, and refused when it cannot run.

## Why this exists

Production lost the API twice to the same failure: the first upload or RAG
query on a 512 MB instance loaded a local ONNX embedding model, the kernel
killed the process, and every request in flight received a 502 for roughly fifty
seconds.

Nothing in the code said the local model needed hundreds of megabytes, so the
only symptom was an instance dying. `ensure_embedding_backend_available()` makes
that requirement explicit and checkable: it resolves the backend from
configuration and refuses the local model when the host does not have room,
raising `EmbeddingsUnavailableError` (503) instead of attempting a load that
kills the process.

These tests pin the decision table, and in particular that a refusal happens
without importing fastembed at all — a guard that loads the model to discover it
cannot load the model is not a guard.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.core.config import settings  # noqa: E402
from backend.app.core.exceptions import EmbeddingsUnavailableError  # noqa: E402
from backend.app.services.rag import retrieval  # noqa: E402


@pytest.fixture(autouse=True)
def _no_gemini_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from "no key configured", the state that caused the OOM."""
    monkeypatch.setattr(settings, "GEMINI_API_KEY", None, raising=False)
    monkeypatch.setattr(retrieval, "get_byok_key", lambda _provider: None, raising=False)


@pytest.fixture
def fail_if_model_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any attempt to construct the local model fails the test immediately."""

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "the local embedding model was constructed; the capacity guard did not run"
        )

    monkeypatch.setattr(retrieval, "_get_fastembed_model", _explode)


def test_gemini_provider_without_a_key_is_a_503_not_a_local_model(
    monkeypatch: pytest.MonkeyPatch, fail_if_model_loads: None
) -> None:
    monkeypatch.setattr(settings, "EMBEDDINGS_PROVIDER", "gemini", raising=False)

    with pytest.raises(EmbeddingsUnavailableError) as excinfo:
        retrieval.ensure_embedding_backend_available()

    assert excinfo.value.status_code == 503
    assert "GEMINI_API_KEY" in str(excinfo.value)


def test_local_model_is_refused_when_the_host_is_too_small(
    monkeypatch: pytest.MonkeyPatch, fail_if_model_loads: None
) -> None:
    monkeypatch.setattr(settings, "EMBEDDINGS_PROVIDER", "auto", raising=False)
    monkeypatch.setattr(retrieval, "_available_ram_mb", lambda: 300.0, raising=False)

    with pytest.raises(EmbeddingsUnavailableError) as excinfo:
        retrieval.ensure_embedding_backend_available()

    assert excinfo.value.status_code == 503
    # The detail has to be actionable: it names both ways out.
    assert "GEMINI_API_KEY" in str(excinfo.value)
    assert "RAM" in str(excinfo.value)


def test_local_model_is_allowed_when_the_host_has_room(
    monkeypatch: pytest.MonkeyPatch, fail_if_model_loads: None
) -> None:
    monkeypatch.setattr(settings, "EMBEDDINGS_PROVIDER", "auto", raising=False)
    monkeypatch.setattr(retrieval, "_available_ram_mb", lambda: 4096.0, raising=False)

    assert retrieval.ensure_embedding_backend_available() == "fastembed"


def test_unknown_ram_does_not_refuse_a_laptop(
    monkeypatch: pytest.MonkeyPatch, fail_if_model_loads: None
) -> None:
    """A host that cannot report memory (dev machines) keeps the old behaviour."""
    monkeypatch.setattr(settings, "EMBEDDINGS_PROVIDER", "auto", raising=False)
    monkeypatch.setattr(retrieval, "_available_ram_mb", lambda: None, raising=False)

    assert retrieval.ensure_embedding_backend_available() == "fastembed"


def test_a_configured_key_selects_the_api_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "EMBEDDINGS_PROVIDER", "auto", raising=False)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key", raising=False)

    assert retrieval.ensure_embedding_backend_available() == "gemini"


# ─── Container memory: the host is not the ceiling ──────────────────────────
#
# The guard is only as good as the number it reads. On the real deployment
# /proc/meminfo reported 11.4GB free inside a 512MB instance (containers see the
# host unless the runtime uses lxcfs), so a host-only reading would have approved
# the local model on exactly the box it cannot fit -- the failure the guard
# exists to prevent. These tests pin that the cgroup is consulted and that the
# smaller of the two answers wins.


def _fake_reader(files: dict[str, str]):
    def _read(path: str) -> str:
        if path not in files:
            raise OSError(path)
        return files[path]

    return _read


def test_container_limit_wins_over_a_much_larger_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        retrieval,
        "_read_text_file",
        _fake_reader(
            {
                "/proc/meminfo": "MemTotal: 16384000 kB\nMemAvailable: 12000000 kB\n",
                "/sys/fs/cgroup/memory.max": str(512 * 1024 * 1024),
                "/sys/fs/cgroup/memory.current": str(200 * 1024 * 1024),
            }
        ),
    )

    free = retrieval.available_ram_mb()

    assert free == pytest.approx(312.0, abs=1.0)
    assert free < settings.FASTEMBED_MIN_FREE_RAM_MB
    assert retrieval.cgroup_limit_mb() == pytest.approx(512.0, abs=0.5)


def test_a_512mb_container_refuses_the_local_model(
    monkeypatch: pytest.MonkeyPatch, fail_if_model_loads: None
) -> None:
    """End to end against the production shape: 512MB cgroup, 11GB host."""
    monkeypatch.setattr(settings, "EMBEDDINGS_PROVIDER", "auto", raising=False)
    monkeypatch.setattr(
        retrieval,
        "_read_text_file",
        _fake_reader(
            {
                "/proc/meminfo": "MemTotal: 16384000 kB\nMemAvailable: 11412000 kB\n",
                "/sys/fs/cgroup/memory.max": str(512 * 1024 * 1024),
                "/sys/fs/cgroup/memory.current": str(180 * 1024 * 1024),
            }
        ),
    )

    with pytest.raises(EmbeddingsUnavailableError) as excinfo:
        retrieval.ensure_embedding_backend_available()

    assert excinfo.value.status_code == 503


def test_unlimited_cgroup_falls_back_to_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        retrieval,
        "_read_text_file",
        _fake_reader(
            {
                "/proc/meminfo": "MemAvailable: 4096000 kB\n",
                "/sys/fs/cgroup/memory.max": "max",
                "/sys/fs/cgroup/memory.current": "1024",
            }
        ),
    )

    assert retrieval.available_ram_mb() == pytest.approx(4000.0, abs=1.0)
    assert retrieval.cgroup_limit_mb() is None


def test_cgroup_v1_sentinel_is_not_treated_as_a_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        retrieval,
        "_read_text_file",
        _fake_reader(
            {
                "/proc/meminfo": "MemAvailable: 2048000 kB\n",
                "/sys/fs/cgroup/memory/memory.limit_in_bytes": str(1 << 62),
                "/sys/fs/cgroup/memory/memory.usage_in_bytes": "1024",
            }
        ),
    )

    assert retrieval.available_ram_mb() == pytest.approx(2000.0, abs=1.0)
    assert retrieval.cgroup_limit_mb() is None
