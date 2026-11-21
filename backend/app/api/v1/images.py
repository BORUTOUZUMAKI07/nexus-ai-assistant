"""Free image-generation API.

Wraps :mod:`backend.app.services.tools.image_generation` (Pollinations.ai,
key-less) so the chat UI can turn a text prompt into an image at zero cost. The
image comes back as a base64 data URL, so the browser never has to reach the
third-party origin itself.
"""

from typing import Any

import structlog
from backend.app.api.deps import get_current_user
from backend.app.core.config import settings
from backend.app.domain.user.models import User
from backend.app.infrastructure.cache.redis_client import redis_service
from backend.app.services.tools.image_generation import (
    DAILY_IMAGE_LIMIT,
    ImageGenerationError,
    image_generation_service,
)
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/images", tags=["images"])


class ImageGenerationRequest(BaseModel):
    model_config = {"extra": "forbid"}

    prompt: str = Field(min_length=1, max_length=settings.IMAGE_MAX_PROMPT_CHARS)
    width: int = Field(default=settings.IMAGE_DEFAULT_WIDTH, ge=256, le=2048)
    height: int = Field(default=settings.IMAGE_DEFAULT_HEIGHT, ge=256, le=2048)


@router.post("/generate", status_code=status.HTTP_200_OK)
async def generate_image_endpoint(
    body: ImageGenerationRequest,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Generate an image from a prompt (free, key-less, via Pollinations).

    Enforces a per-user daily cap (DAILY_IMAGE_LIMIT) keyed by user id with a
    24h sliding window. Redis outages fail open so a cache failure never blocks
    generation.

    Returns ``image_data_url`` (a ``data:image/...;base64,...`` string ready to
    drop into an ``<img>``), the resolved ``model`` and the ``prompt`` echoed
    back. A provider failure is a ``502`` -- the request was valid and the
    upstream free service could not serve it, which is a different thing from
    the caller's mistake.
    """
    if not settings.IMAGE_GENERATION_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Image generation is disabled",
        )

    # Per-user daily cap (free-tier abuse guard). Keyed per user id so each
    # account gets its own 50-image/24h bucket. Fail-open on Redis outage.
    try:
        allowed, remaining = await redis_service.check_rate_limit(
            f"image_gen:user:{current_user.id}",
            limit=DAILY_IMAGE_LIMIT,
            window_seconds=86400,
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Daily image generation limit of {DAILY_IMAGE_LIMIT} reached. "
                    f"Try again tomorrow."
                ),
                headers={
                    "X-RateLimit-Limit": str(DAILY_IMAGE_LIMIT),
                    "X-RateLimit-Remaining": str(remaining),
                    "Retry-After": "86400",
                },
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "image_generation_rate_limit_check_failed_fail_open",
            user_id=str(current_user.id),
            error=str(exc),
        )

    try:
        result = await image_generation_service.generate(
            body.prompt,
            width=body.width,
            height=body.height,
        )
    except ImageGenerationError as exc:
        logger.warning(
            "image_generation_failed",
            user_id=str(current_user.id),
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Image generation failed: {exc}",
        )

    logger.info(
        "image_generated",
        user_id=str(current_user.id),
        model=result["model"],
        bytes=result["bytes"],
    )
    return {
        "image_data_url": result["data_url"],
        "prompt": body.prompt,
        "model": f"pollinations/{result['model']}",
        "width": result["width"],
        "height": result["height"],
    }
