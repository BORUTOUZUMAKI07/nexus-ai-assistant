"""Free, key-less image generation via Pollinations.ai.

Pollinations renders an image straight off a URL -- no API key, no account, no
billing -- which is the only image generator that is genuinely free in this
deployment (Gemini's image models carry ``limit: 0`` on the free tier, verified
2026-10-14).

The service fetches the bytes **server-side** rather than handing the browser a
Pollinations URL, for three reasons:

* the free endpoint is rate-limited and occasionally 5xx's on one model while
  another answers, so the retry/fallback belongs in one place;
* a returned URL is a third-party origin the transcript would have to reach at
  render time, which fails behind a locked-down network and leaks the prompt to
  the browser's history;
* a base64 data URL renders inline with no extra fetch, so the image is still
  there when the network drops.

This module is deliberately pure and dependency-injected: the HTTP client is a
constructor argument so the whole thing is testable without a network, exactly
like the other ``services/tools`` modules.
"""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote

import httpx
import structlog

logger = structlog.get_logger(__name__)

POLLINATIONS_PROMPT_URL = "https://image.pollinations.ai/prompt"

# Ordered best-quality first. Each model is tried before giving up, because the
# free endpoint intermittently 5xx's on one model while another answers.
MODEL_FALLBACKS: tuple[str, ...] = ("flux", "turbo")

# Only raster formats Pollinations actually returns. A text/html body (a
# captive portal, an error page shipping a 200) must never be base64'd and
# labelled an image.
_ALLOWED_MIME: frozenset[str] = frozenset({"image/jpeg", "image/png", "image/webp"})

# A hard ceiling on the prompt text. The free endpoint accepts much longer, but
# beyond this the URL grows past what some proxies will forward, and the prompt
# is already far past the point where more words change the picture.
MAX_PROMPT_CHARS = 1000


class ImageGenerationError(RuntimeError):
    """Raised when no model in the fallback chain produced a real image."""


class ImageGenerationService:
    """Generate an image from a text prompt, free of charge.

    ``client_factory`` returns an async context manager yielding a client. It
    defaults to a real :class:`httpx.AsyncClient`; tests inject a fake so the
    suite never reaches the network. ``timeout`` bounds a single provider call
    (Pollinations is slow on the first render of a prompt, so this is generous).
    """

    def __init__(
        self,
        timeout: float = 60.0,
        client_factory: Any | None = None,
    ) -> None:
        self._timeout = timeout
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        )

    async def generate(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        seed: int | None = None,
    ) -> dict[str, Any]:
        """Return a rendered image as a base64 data URL.

        Raises :class:`ImageGenerationError` if the prompt is empty or every
        model in the fallback chain fails. Success returns a dict with
        ``data_url``, ``mime``, ``model``, ``width``, ``height`` and ``bytes``.
        """
        cleaned = (prompt or "").strip()
        if not cleaned:
            raise ImageGenerationError("prompt must not be empty")
        if len(cleaned) > MAX_PROMPT_CHARS:
            cleaned = cleaned[:MAX_PROMPT_CHARS]

        encoded_prompt = quote(cleaned, safe="")
        last_error: str = "image generation failed"

        async with self._client_factory() as client:
            for model in MODEL_FALLBACKS:
                params: dict[str, Any] = {
                    "width": width,
                    "height": height,
                    "nologo": "true",
                    "model": model,
                }
                if seed is not None:
                    params["seed"] = int(seed)
                try:
                    response = await client.get(
                        f"{POLLINATIONS_PROMPT_URL}/{encoded_prompt}",
                        params=params,
                    )
                except Exception as exc:  # noqa: BLE001 - one model failing must not stop the chain
                    last_error = f"{model}: {exc}"
                    logger.warning("image_generation_request_failed", model=model, error=str(exc))
                    continue

                if response.status_code != 200:
                    last_error = f"{model}: HTTP {response.status_code}"
                    logger.warning(
                        "image_generation_http_error",
                        model=model,
                        status=response.status_code,
                    )
                    continue

                mime = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
                if mime not in _ALLOWED_MIME:
                    last_error = f"{model}: unexpected content-type {mime or 'unknown'}"
                    logger.warning("image_generation_bad_content_type", model=model, mime=mime)
                    continue

                data = response.content
                if not data:
                    last_error = f"{model}: empty body"
                    logger.warning("image_generation_empty_body", model=model)
                    continue

                encoded = base64.b64encode(data).decode("ascii")
                return {
                    "data_url": f"data:{mime};base64,{encoded}",
                    "mime": mime,
                    "model": model,
                    "width": width,
                    "height": height,
                    "bytes": len(data),
                }

        raise ImageGenerationError(last_error)


# Process-wide singleton. Constructed with no client, i.e. it uses a fresh
# httpx client per call -- the free endpoint is called rarely and a shared
# connection pool is not worth the lifecycle.
image_generation_service = ImageGenerationService()
