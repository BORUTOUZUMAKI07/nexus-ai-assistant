"""
Audio Transcription API Router.
Provides fast, free speech-to-text using Groq Whisper.
"""
import io
import structlog
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from backend.app.api.deps import get_current_user
from backend.app.domain.user.models import User
from backend.app.infrastructure.ai.litellm_client import ai_client

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/audio", tags=["audio"])

ALLOWED_AUDIO_EXTENSIONS = {
    ".webm", ".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac", ".mp4"
}


@router.post("/transcribe", status_code=status.HTTP_200_OK)
async def transcribe_audio_endpoint(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """
    Transcribes an uploaded audio recording into text using Groq Whisper (whisper-large-v3).
    Operates at 0 MB local RAM usage on Render.
    """
    filename = file.filename or "recording.webm"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ".webm"

    if ext not in ALLOWED_AUDIO_EXTENSIONS and not (file.content_type and "audio" in file.content_type):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported audio format '{ext}'. Allowed: {', '.join(ALLOWED_AUDIO_EXTENSIONS)}",
        )

    try:
        content = await file.read()
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Empty audio recording received",
            )

        # LiteLLM/Groq expects a file-like object with a recognizable filename
        buf = io.BytesIO(content)
        buf.name = filename

        transcription = await ai_client.transcribe_audio(buf)
        logger.info(
            "audio_transcription_succeeded",
            user_id=str(current_user.id),
            filename=filename,
            char_count=len(transcription),
        )
        return {"text": transcription.strip()}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "audio_transcription_failed",
            user_id=str(current_user.id),
            error=str(exc),
            filename=filename,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Audio transcription failed: {str(exc)}",
        )
