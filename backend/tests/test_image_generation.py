"""Tests for free image generation (Pollinations) — service and route.

Zero-cost image generation is the one capability with no API key behind it, so
the failure modes are all *provider* failures: a 5xx on one model, an HTML error
page shipped with a 200, an empty body, a dropped connection. The service's
whole job is to walk a small model chain and only ever hand back real image
bytes; the route's job is to map that outcome to 200 / 502 / 503 without ever
base64-ing a non-image.

No network: the HTTP client is injected (that is why the service takes a
``client_factory``), and the route is exercised directly with a fake user.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from backend.app.api.v1 import images as images_mod
from backend.app.services.tools.image_generation import (
    MAX_PROMPT_CHARS,
    ImageGenerationError,
    ImageGenerationService,
)
from fastapi import HTTPException

JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
PNG = b"\x89PNG\r\n\x1a\nfake-png-bytes"


# ── doubles ──────────────────────────────────────────────────────────────────


class _Response:
    def __init__(self, status_code: int = 200, content: bytes = JPEG, content_type: str = "image/jpeg"):
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": content_type}


class _FakeClient:
    """Returns a per-model response (or raises the per-model exception)."""

    def __init__(self, by_model: dict[str, Any]) -> None:
        self._by_model = by_model
        self.calls: list[tuple[str, str]] = []

    async def get(self, url: str, params: dict[str, Any] | None = None, **_: Any) -> _Response:
        model = str((params or {}).get("model"))
        self.calls.append((model, url))
        result = self._by_model[model]
        if isinstance(result, Exception):
            raise result
        return result


class _ClientFactory:
    def __init__(self, client: _FakeClient) -> None:
        self._client = client

    def __call__(self) -> _ClientFactory:
        return self

    async def __aenter__(self) -> _FakeClient:
        return self._client

    async def __aexit__(self, *_: Any) -> bool:
        return False


def _service(by_model: dict[str, Any]) -> tuple[ImageGenerationService, _FakeClient]:
    client = _FakeClient(by_model)
    return ImageGenerationService(client_factory=_ClientFactory(client)), client


# ── service ──────────────────────────────────────────────────────────────────


async def test_an_empty_prompt_is_refused_before_any_call():
    service, client = _service({"flux": _Response()})

    with pytest.raises(ImageGenerationError):
        await service.generate("   ")

    assert client.calls == [], "an empty prompt must not reach the provider"


async def test_a_prompt_longer_than_the_cap_is_truncated_in_the_request():
    """The URL is what proxies forward; an unbounded prompt breaks the request."""
    service, client = _service({"flux": _Response()})

    await service.generate("x" * (MAX_PROMPT_CHARS + 500))

    _, url = client.calls[0]
    # quote("x"*n, safe="") == "x"*n, so the tail length is the truncated length.
    assert url.endswith("x" * MAX_PROMPT_CHARS)
    assert not url.endswith("x" * (MAX_PROMPT_CHARS + 1))


async def test_a_successful_render_returns_a_base64_data_url():
    service, _ = _service({"flux": _Response(content=JPEG, content_type="image/jpeg")})

    result = await service.generate("a red panda", width=512, height=768)

    assert result["data_url"].startswith("data:image/jpeg;base64,")
    assert result["mime"] == "image/jpeg"
    assert result["model"] == "flux"
    assert result["width"] == 512
    assert result["height"] == 768
    assert result["bytes"] == len(JPEG)


async def test_the_first_model_5xx_falls_through_to_the_second():
    service, client = _service(
        {
            "flux": _Response(status_code=500, content=b"", content_type="text/html"),
            "turbo": _Response(content=PNG, content_type="image/png"),
        }
    )

    result = await service.generate("cat")

    assert result["model"] == "turbo"
    assert [m for m, _ in client.calls] == ["flux", "turbo"]


async def test_an_html_body_shipped_with_a_200_is_not_base64d_as_an_image():
    """A captive portal / error page must never become an ``<img>`` src."""
    service, _ = _service(
        {
            "flux": _Response(content=b"<html>nope</html>", content_type="text/html"),
            "turbo": _Response(content=PNG, content_type="image/png"),
        }
    )

    result = await service.generate("dog")

    assert result["model"] == "turbo"
    assert result["mime"] == "image/png"


async def test_an_empty_200_body_falls_through():
    service, _ = _service(
        {
            "flux": _Response(content=b"", content_type="image/jpeg"),
            "turbo": _Response(content=JPEG, content_type="image/jpeg"),
        }
    )

    assert (await service.generate("dog"))["model"] == "turbo"


async def test_a_connection_error_falls_through_instead_of_raising():
    service, client = _service(
        {
            "flux": RuntimeError("connection reset"),
            "turbo": _Response(content=JPEG, content_type="image/jpeg"),
        }
    )

    assert (await service.generate("dog"))["model"] == "turbo"
    assert [m for m, _ in client.calls] == ["flux", "turbo"]


async def test_every_model_failing_raises():
    service, _ = _service(
        {
            "flux": _Response(status_code=502, content=b"", content_type="text/html"),
            "turbo": _Response(status_code=502, content=b"", content_type="text/html"),
        }
    )

    with pytest.raises(ImageGenerationError):
        await service.generate("dog")


# ── route ────────────────────────────────────────────────────────────────────


class _FakeImageService:
    def __init__(self, *, result: dict[str, Any] | None = None, error: Exception | None = None):
        self._result = result
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def generate(self, prompt: str, width: int, height: int) -> dict[str, Any]:
        self.calls.append({"prompt": prompt, "width": width, "height": height})
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


_USER = SimpleNamespace(id="11111111-1111-1111-1111-111111111111")


async def _call_route(body: images_mod.ImageGenerationRequest):
    return await images_mod.generate_image_endpoint(body=body, current_user=_USER)


async def test_the_route_reports_503_when_image_generation_is_disabled(monkeypatch):
    monkeypatch.setattr(images_mod.settings, "IMAGE_GENERATION_ENABLED", False)
    # A service stub too, so a reverted gate surfaces as "did not raise" rather
    # than reaching the real provider from the test suite.
    service = _FakeImageService(
        result={
            "data_url": "data:image/jpeg;base64,AAAA",
            "mime": "image/jpeg",
            "model": "flux",
            "width": 1024,
            "height": 1024,
            "bytes": 3,
        }
    )
    monkeypatch.setattr(images_mod, "image_generation_service", service)

    with pytest.raises(HTTPException) as exc:
        await _call_route(images_mod.ImageGenerationRequest(prompt="cat"))

    assert exc.value.status_code == 503
    assert service.calls == [], "a disabled deployment must not reach the provider"


async def test_the_route_maps_a_provider_failure_to_502(monkeypatch):
    monkeypatch.setattr(images_mod.settings, "IMAGE_GENERATION_ENABLED", True)
    monkeypatch.setattr(
        images_mod,
        "image_generation_service",
        _FakeImageService(error=ImageGenerationError("flux: HTTP 502")),
    )

    with pytest.raises(HTTPException) as exc:
        await _call_route(images_mod.ImageGenerationRequest(prompt="cat"))

    assert exc.value.status_code == 502


async def test_the_route_returns_a_data_url_and_a_pollinations_model_name(monkeypatch):
    monkeypatch.setattr(images_mod.settings, "IMAGE_GENERATION_ENABLED", True)
    service = _FakeImageService(
        result={
            "data_url": "data:image/jpeg;base64,AAAA",
            "mime": "image/jpeg",
            "model": "flux",
            "width": 1024,
            "height": 1024,
            "bytes": 3,
        }
    )
    monkeypatch.setattr(images_mod, "image_generation_service", service)

    out = await _call_route(images_mod.ImageGenerationRequest(prompt="a red panda"))

    assert out["image_data_url"] == "data:image/jpeg;base64,AAAA"
    assert out["model"] == "pollinations/flux"
    assert out["prompt"] == "a red panda"
    assert service.calls == [{"prompt": "a red panda", "width": 1024, "height": 1024}]


async def test_the_route_passes_the_requested_dimensions_through(monkeypatch):
    monkeypatch.setattr(images_mod.settings, "IMAGE_GENERATION_ENABLED", True)
    service = _FakeImageService(
        result={
            "data_url": "data:image/png;base64,AAAA",
            "mime": "image/png",
            "model": "turbo",
            "width": 512,
            "height": 256,
            "bytes": 3,
        }
    )
    monkeypatch.setattr(images_mod, "image_generation_service", service)

    out = await _call_route(
        images_mod.ImageGenerationRequest(prompt="wide", width=512, height=256)
    )

    assert service.calls == [{"prompt": "wide", "width": 512, "height": 256}]
    assert out["width"] == 512 and out["height"] == 256
