"""
Unit tests for text-to-speech synthesis: provider-key guard (501-equivalent at
service level) and successful byte output with a stubbed speech API.
"""
import pytest
from backend.app.core.config import settings
from backend.app.infrastructure.ai import litellm_client


def test_synthesize_speech_raises_without_provider_key(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
    monkeypatch.setattr(settings, "TOGETHER_API_KEY", None)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None)

    import asyncio

    with pytest.raises(RuntimeError, match="speech-capable provider"):
        asyncio.get_event_loop().run_until_complete(
            litellm_client.ai_client.synthesize_speech("hello world")
        )


def test_synthesize_speech_returns_audio_bytes(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "TOGETHER_API_KEY", None)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None)

    async def fake_aspeech(**kwargs):
        assert kwargs["input"] == "hello world"
        assert kwargs["voice"] == settings.TTS_VOICE
        return b"\x00audio-data"

    monkeypatch.setattr(litellm_client.litellm, "aspeech", fake_aspeech)

    import asyncio

    audio = asyncio.get_event_loop().run_until_complete(
        litellm_client.ai_client.synthesize_speech("hello world")
    )
    assert audio == b"\x00audio-data"


def test_synthesize_speech_uses_provider_matching_key(monkeypatch):
    """Only an OpenRouter key + openai-routed default → clean RuntimeError (501),
    while an explicit openrouter/… model uses the OpenRouter key."""
    import asyncio

    monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
    monkeypatch.setattr(settings, "TOGETHER_API_KEY", None)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-or-test")

    captured = {}

    async def fake_aspeech(**kwargs):
        captured.update(kwargs)
        return b"\x00audio"

    monkeypatch.setattr(litellm_client.litellm, "aspeech", fake_aspeech)
    loop = asyncio.get_event_loop()

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        loop.run_until_complete(litellm_client.ai_client.synthesize_speech("x"))

    audio = loop.run_until_complete(
        litellm_client.ai_client.synthesize_speech("x", model="openrouter/tts-audio")
    )
    assert audio == b"\x00audio"
    assert captured["api_key"] == "sk-or-test"