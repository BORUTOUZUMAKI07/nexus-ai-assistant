"""
Unit tests for text-to-speech synthesis via Microsoft Edge neural voices
(edge-tts): free and key-less — no provider key is ever required — with a
stubbed edge_tts.Communicate for the audio-stream path.
"""
import edge_tts
import pytest
from backend.app.core.config import settings
from backend.app.infrastructure.ai import litellm_client


@pytest.mark.asyncio
async def test_synthesize_speech_works_without_any_provider_key(monkeypatch):
    """TTS must not require any API key — edge-tts needs none."""

    class FakeCommunicate:
        instances = []

        def __init__(self, text, voice):
            self.text = text
            self.voice = voice
            FakeCommunicate.instances.append(self)

        async def stream(self):
            yield {"type": "audio", "data": b"\x00audio-frame1"}
            yield {"type": "WordBoundary", "data": b""}
            yield {"type": "audio", "data": b"\x00audio-frame2"}

    monkeypatch.setattr(edge_tts, "Communicate", FakeCommunicate)

    audio = await litellm_client.ai_client.synthesize_speech("hello world")

    assert audio == b"\x00audio-frame1\x00audio-frame2"
    assert FakeCommunicate.instances[0].text == "hello world"
    assert FakeCommunicate.instances[0].voice == settings.TTS_VOICE


@pytest.mark.asyncio
async def test_synthesize_speech_uses_voice_override(monkeypatch):
    class FakeCommunicate:
        instances = []

        def __init__(self, text, voice):
            self.voice = voice
            FakeCommunicate.instances.append(self)

        async def stream(self):
            yield {"type": "audio", "data": b"\x00audio"}

    monkeypatch.setattr(edge_tts, "Communicate", FakeCommunicate)

    await litellm_client.ai_client.synthesize_speech("x", voice="en-IE-EmilyNeural")
    assert FakeCommunicate.instances[0].voice == "en-IE-EmilyNeural"


@pytest.mark.asyncio
async def test_synthesize_speech_raises_when_no_audio_returned(monkeypatch):
    class FakeCommunicate:
        def __init__(self, text, voice):
            pass

        async def stream(self):
            yield {"type": "WordBoundary", "data": b""}

    monkeypatch.setattr(edge_tts, "Communicate", FakeCommunicate)

    with pytest.raises(RuntimeError, match="no audio"):
        await litellm_client.ai_client.synthesize_speech("hello")
