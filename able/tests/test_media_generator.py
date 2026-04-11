"""Tests for F12 — Media Generation Fallback."""

import pytest
from able.tools.media.generator import (
    MediaType, MediaRequest, MediaResult, MediaGenerator,
    PlaceholderProvider, detect_media_intent,
)


class TestDetectMediaIntent:

    def test_image_generation(self):
        assert detect_media_intent("generate an image of a cat") == MediaType.IMAGE
        assert detect_media_intent("create a picture showing sunset") == MediaType.IMAGE
        assert detect_media_intent("make me a logo for my startup") == MediaType.IMAGE

    def test_audio_generation(self):
        assert detect_media_intent("generate audio narration") == MediaType.AUDIO
        assert detect_media_intent("create a voice recording") == MediaType.AUDIO
        assert detect_media_intent("text-to-speech this paragraph") == MediaType.AUDIO

    def test_video_generation(self):
        assert detect_media_intent("generate a video clip") == MediaType.VIDEO
        assert detect_media_intent("create an animation") == MediaType.VIDEO

    def test_no_media_intent(self):
        assert detect_media_intent("write a Python function") is None
        assert detect_media_intent("fix the login bug") is None
        assert detect_media_intent("deploy to production") is None

    def test_brand_name_detection(self):
        assert detect_media_intent("use DALL-E for this") == MediaType.IMAGE
        assert detect_media_intent("try ElevenLabs") == MediaType.AUDIO
        assert detect_media_intent("use Runway for video") == MediaType.VIDEO


class TestMediaResult:

    def test_success_with_url(self):
        r = MediaResult(media_type=MediaType.IMAGE, provider="dall-e", url="https://example.com/img.png")
        assert r.success

    def test_success_with_file(self):
        r = MediaResult(media_type=MediaType.AUDIO, provider="elevenlabs", file_path="/tmp/audio.mp3")
        assert r.success

    def test_failure(self):
        r = MediaResult(media_type=MediaType.IMAGE, provider="dall-e", error="API key invalid")
        assert not r.success

    def test_placeholder(self):
        r = MediaResult(media_type=MediaType.IMAGE, provider="placeholder", is_placeholder=True, error="No provider")
        assert not r.success
        assert r.is_placeholder


class TestPlaceholderProvider:

    @pytest.mark.asyncio
    async def test_always_available(self):
        p = PlaceholderProvider(MediaType.IMAGE)
        assert p.is_available()

    @pytest.mark.asyncio
    async def test_returns_placeholder(self):
        p = PlaceholderProvider(MediaType.IMAGE)
        result = await p.generate(MediaRequest(prompt="test", media_type=MediaType.IMAGE))
        assert result.is_placeholder
        assert "No image provider" in result.error


class TestMediaGenerator:

    def test_available_providers(self):
        gen = MediaGenerator()
        providers = gen.available_providers()
        assert "image" in providers
        assert "audio" in providers
        assert "video" in providers

    @pytest.mark.asyncio
    async def test_fallback_to_placeholder(self):
        gen = MediaGenerator()
        result = await gen.generate(MediaRequest(
            prompt="test image",
            media_type=MediaType.VIDEO,  # No video providers configured
        ))
        assert result.is_placeholder or result.error
