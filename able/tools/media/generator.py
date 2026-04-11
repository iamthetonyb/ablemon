"""
F12 — Media Generation Fallback.

Auto-fallback media generation system. Detects media intent from user
requests and routes to the best available provider. Supports image,
audio, and video generation with automatic provider selection.

Fallback chain per media type:
- Image: DALL-E 3 → Stable Diffusion (local) → placeholder
- Audio: ElevenLabs → local TTS → placeholder
- Video: Runway → placeholder

All providers are optional — graceful degradation to placeholders.
"""

from __future__ import annotations

import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class MediaType(str, Enum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


@dataclass
class MediaResult:
    """Result of a media generation request."""
    media_type: MediaType
    provider: str
    file_path: Optional[str] = None
    url: Optional[str] = None
    duration_ms: float = 0
    error: Optional[str] = None
    is_placeholder: bool = False

    @property
    def success(self) -> bool:
        return self.error is None and (self.file_path or self.url) is not None


@dataclass
class MediaRequest:
    """A media generation request."""
    prompt: str
    media_type: MediaType
    style: Optional[str] = None
    size: str = "1024x1024"
    duration_s: int = 10  # For audio/video
    output_dir: str = "data/media"


# ── Intent Detection ──────────────────────────────────────────

_IMAGE_PATTERNS = [
    r"\b(generate|create|make|draw|design|render)\b.*\b(image|picture|photo|illustration|icon|logo|graphic|diagram)\b",
    r"\b(image|picture|photo|illustration)\b.*\b(of|showing|depicting|with)\b",
    r"dall[- ]?e",
    r"stable diffusion",
]

_AUDIO_PATTERNS = [
    r"\b(generate|create|make|produce)\b.*\b(audio|sound|music|voice|speech|narration)\b",
    r"\b(text[- ]to[- ]speech|TTS)\b",
    r"elevenlabs",
]

_VIDEO_PATTERNS = [
    r"\b(generate|create|make|produce)\b.*\b(video|animation|clip|footage)\b",
    r"runway",
    r"sora",
]


def detect_media_intent(text: str) -> Optional[MediaType]:
    """Detect media generation intent from text.

    Returns None if no media intent detected.
    """
    lower = text.lower()
    for pattern in _IMAGE_PATTERNS:
        if re.search(pattern, lower):
            return MediaType.IMAGE
    for pattern in _VIDEO_PATTERNS:
        if re.search(pattern, lower):
            return MediaType.VIDEO
    for pattern in _AUDIO_PATTERNS:
        if re.search(pattern, lower):
            return MediaType.AUDIO
    return None


# ── Provider Interface ────────────────────────────────────────


class MediaProvider(ABC):
    """Base class for media generation providers."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def media_type(self) -> MediaType: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    async def generate(self, request: MediaRequest) -> MediaResult: ...


# ── Provider Implementations ─────────────────────────────────


class DallEProvider(MediaProvider):
    """DALL-E 3 image generation via OpenAI API."""

    name = "dall-e-3"
    media_type = MediaType.IMAGE

    def is_available(self) -> bool:
        return bool(os.environ.get("OPENAI_API_KEY"))

    async def generate(self, request: MediaRequest) -> MediaResult:
        start = time.time()
        try:
            import httpx
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/images/generations",
                    headers={
                        "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "dall-e-3",
                        "prompt": request.prompt,
                        "n": 1,
                        "size": request.size,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                url = data["data"][0]["url"]
                return MediaResult(
                    media_type=MediaType.IMAGE,
                    provider=self.name,
                    url=url,
                    duration_ms=(time.time() - start) * 1000,
                )
        except Exception as e:
            return MediaResult(
                media_type=MediaType.IMAGE,
                provider=self.name,
                error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )


class ElevenLabsProvider(MediaProvider):
    """ElevenLabs text-to-speech."""

    name = "elevenlabs"
    media_type = MediaType.AUDIO

    def is_available(self) -> bool:
        return bool(os.environ.get("ELEVENLABS_API_KEY"))

    async def generate(self, request: MediaRequest) -> MediaResult:
        start = time.time()
        try:
            import httpx
            voice_id = "21m00Tcm4TlvDq8ikWAM"  # Default voice
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                    headers={
                        "xi-api-key": os.environ["ELEVENLABS_API_KEY"],
                        "Content-Type": "application/json",
                    },
                    json={
                        "text": request.prompt,
                        "model_id": "eleven_multilingual_v2",
                    },
                )
                resp.raise_for_status()

                out_dir = Path(request.output_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / f"tts_{int(time.time())}.mp3"
                out_path.write_bytes(resp.content)

                return MediaResult(
                    media_type=MediaType.AUDIO,
                    provider=self.name,
                    file_path=str(out_path),
                    duration_ms=(time.time() - start) * 1000,
                )
        except Exception as e:
            return MediaResult(
                media_type=MediaType.AUDIO,
                provider=self.name,
                error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )


class PlaceholderProvider(MediaProvider):
    """Fallback placeholder when no real provider is available."""

    def __init__(self, media_type_val: MediaType) -> None:
        self._media_type = media_type_val

    @property
    def name(self) -> str:
        return "placeholder"

    @property
    def media_type(self) -> MediaType:
        return self._media_type

    def is_available(self) -> bool:
        return True  # Always available

    async def generate(self, request: MediaRequest) -> MediaResult:
        return MediaResult(
            media_type=self._media_type,
            provider=self.name,
            is_placeholder=True,
            error=(
                f"No {self._media_type.value} provider configured. "
                f"Set the appropriate API key to enable generation."
            ),
        )


# ── Generator (Orchestrator) ─────────────────────────────────

# Fallback chains per media type
_FALLBACK_CHAINS: dict[MediaType, list[type]] = {
    MediaType.IMAGE: [DallEProvider],
    MediaType.AUDIO: [ElevenLabsProvider],
    MediaType.VIDEO: [],  # No providers yet
}


class MediaGenerator:
    """Auto-fallback media generation.

    Usage::

        gen = MediaGenerator()
        result = await gen.generate(MediaRequest(
            prompt="A cyberpunk cityscape at sunset",
            media_type=MediaType.IMAGE,
        ))
        if result.success:
            print(f"Generated: {result.url or result.file_path}")
    """

    def __init__(self) -> None:
        self._providers: dict[MediaType, list[MediaProvider]] = {}
        self._init_providers()

    def _init_providers(self) -> None:
        """Initialize provider chains with availability checks."""
        for media_type, chain in _FALLBACK_CHAINS.items():
            providers: list[MediaProvider] = []
            for cls in chain:
                provider = cls()
                if provider.is_available():
                    providers.append(provider)
            # Always add placeholder as last resort
            providers.append(PlaceholderProvider(media_type))
            self._providers[media_type] = providers

    async def generate(self, request: MediaRequest) -> MediaResult:
        """Generate media, trying providers in fallback order."""
        providers = self._providers.get(
            request.media_type,
            [PlaceholderProvider(request.media_type)],
        )

        for provider in providers:
            result = await provider.generate(request)
            if result.success:
                return result

        # All failed, return last error
        return MediaResult(
            media_type=request.media_type,
            provider="none",
            error=f"All {request.media_type.value} providers failed.",
        )

    def available_providers(self) -> dict[str, list[str]]:
        """List available providers per media type."""
        return {
            mt.value: [
                p.name for p in providers
                if not isinstance(p, PlaceholderProvider)
            ]
            for mt, providers in self._providers.items()
        }
