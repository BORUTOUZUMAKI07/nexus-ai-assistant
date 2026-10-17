"""
Speech-to-text now has a fallback.

Groq Whisper was the only STT provider the app could reach, and unlike the chat
path it has no router fallback (litellm's ``atranscription`` supports
openai/azure/nvidia_riva/soniox/bedrock, and Groq is reached as the
openai-compatible one). So a network-level denial of Groq — the 403-on-egress
symptom recorded for the chat providers under some VPNs — took STT down on its
own while chat survived. These tests pin the fallback to Gemini, and pin that
it does not fire when it should not.
"""
from __future__ import annotations

import base64
import io

import pytest
from backend.app.infrastructure.ai import litellm_client


class _Transcription:
    def __init__(self, text: str) -> None:
        self.text = text


class _Message:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str | None) -> None:
        self.message = _Message(content)


class _Completion:
    def __init__(self, content: str | None) -> None:
        self.choices = [_Choice(content)]


def _audio(name: str = "recording.webm", data: bytes = b"\x1aE\xdf\xa3payload") -> io.BytesIO:
    buf = io.BytesIO(data)
    buf.name = name
    return buf


@pytest.fixture(autouse=True)
def _gemini_key(monkeypatch):
    """Give the fallback a key regardless of the checkout's env."""
    monkeypatch.setattr(litellm_client.settings, "GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setattr(litellm_client.settings, "STT_FALLBACK_ENABLED", True)


@pytest.mark.asyncio
async def test_primary_provider_answers_without_touching_the_fallback(monkeypatch):
    calls: list[dict] = []

    async def fake_atranscription(**kwargs):
        calls.append(kwargs)
        return _Transcription("hello from groq")

    async def fake_acompletion(**kwargs):  # pragma: no cover - must not run
        raise AssertionError("fallback must not be called when Groq succeeds")

    monkeypatch.setattr(litellm_client.litellm, "atranscription", fake_atranscription)
    monkeypatch.setattr(litellm_client.litellm, "acompletion", fake_acompletion)

    text = await litellm_client.ai_client.transcribe_audio(_audio())

    assert text == "hello from groq"
    assert calls[0]["model"] == litellm_client.settings.STT_PRIMARY_MODEL


@pytest.mark.asyncio
async def test_groq_failure_falls_back_to_gemini_with_the_audio(monkeypatch):
    """The load-bearing path: primary raises, Gemini gets the same bytes."""

    async def fake_atranscription(**kwargs):
        raise RuntimeError("Access denied. Please check your network settings.")

    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _Completion("hello from gemini")

    monkeypatch.setattr(litellm_client.litellm, "atranscription", fake_atranscription)
    monkeypatch.setattr(litellm_client.litellm, "acompletion", fake_acompletion)

    raw = b"\x1aE\xdf\xa3the-actual-audio"
    text = await litellm_client.ai_client.transcribe_audio(_audio(data=raw))

    assert text == "hello from gemini"
    assert captured["model"] == litellm_client.settings.STT_FALLBACK_MODEL
    assert captured["api_key"] == "test-gemini-key"

    # The audio travels as an inline base64 data URL, tagged with the MIME type
    # derived from the filename — not the wrong format.
    content = captured["messages"][0]["content"]
    file_part = next(p for p in content if p["type"] == "file")
    file_data = file_part["file"]["file_data"]
    mime, b64 = file_data.split(";base64,")
    assert mime == "data:audio/webm"
    assert base64.b64decode(b64) == raw


@pytest.mark.asyncio
async def test_the_fallback_reads_the_audio_the_primary_already_consumed(monkeypatch):
    """A failed primary has advanced the stream; the fallback must rewind it.

    Without the ``seek(0)`` the fallback would read zero bytes — a silent empty
    transcript rather than an error.
    """

    async def fake_atranscription(**kwargs):
        kwargs["file"].read()  # consume the whole stream, exactly as litellm does
        raise RuntimeError("groq down")

    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _Completion("ok")

    monkeypatch.setattr(litellm_client.litellm, "atranscription", fake_atranscription)
    monkeypatch.setattr(litellm_client.litellm, "acompletion", fake_acompletion)

    raw = b"\x1aE\xdf\xa3the-actual-audio"
    await litellm_client.ai_client.transcribe_audio(_audio(data=raw))

    file_data = next(
        p for p in captured["messages"][0]["content"] if p["type"] == "file"
    )["file"]["file_data"]
    assert base64.b64decode(file_data.split(";base64,")[1]) == raw


@pytest.mark.asyncio
async def test_disabled_fallback_re_raises_the_primary_error(monkeypatch):
    monkeypatch.setattr(litellm_client.settings, "STT_FALLBACK_ENABLED", False)

    async def fake_atranscription(**kwargs):
        raise RuntimeError("groq down")

    async def fake_acompletion(**kwargs):  # pragma: no cover - must not run
        raise AssertionError("fallback disabled, must not be called")

    monkeypatch.setattr(litellm_client.litellm, "atranscription", fake_atranscription)
    monkeypatch.setattr(litellm_client.litellm, "acompletion", fake_acompletion)

    with pytest.raises(RuntimeError, match="groq down"):
        await litellm_client.ai_client.transcribe_audio(_audio())


@pytest.mark.asyncio
async def test_missing_gemini_key_fails_loudly_not_silently(monkeypatch):
    monkeypatch.setattr(litellm_client.settings, "GEMINI_API_KEY", None)

    async def fake_atranscription(**kwargs):
        raise RuntimeError("groq down")

    monkeypatch.setattr(litellm_client.litellm, "atranscription", fake_atranscription)

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        await litellm_client.ai_client.transcribe_audio(_audio())


@pytest.mark.asyncio
async def test_both_providers_failing_surfaces_the_fallback_error(monkeypatch):
    async def fake_atranscription(**kwargs):
        raise RuntimeError("groq down")

    async def fake_acompletion(**kwargs):
        raise RuntimeError("gemini also down")

    monkeypatch.setattr(litellm_client.litellm, "atranscription", fake_atranscription)
    monkeypatch.setattr(litellm_client.litellm, "acompletion", fake_acompletion)

    with pytest.raises(RuntimeError, match="gemini also down"):
        await litellm_client.ai_client.transcribe_audio(_audio())


def test_audio_mime_type_maps_the_formats_the_endpoint_allows():
    assert litellm_client._audio_mime_type("recording.webm") == "audio/webm"
    assert litellm_client._audio_mime_type("clip.MP3") == "audio/mpeg"
    assert litellm_client._audio_mime_type("voice.wav") == "audio/wav"
    # Unknown/empty names fall back to what the browser actually sends.
    assert litellm_client._audio_mime_type("") == "audio/webm"
    assert litellm_client._audio_mime_type("mystery.bin") == "audio/webm"
