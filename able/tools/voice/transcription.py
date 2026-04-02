"""
Voice Transcription — Pluggable ASR backends.

Supports:
- External HTTP ASR endpoint (recommended: Voxtral, Qwen3, or any audio-native model)
- OpenAI Whisper API (legacy fallback)
- Local Whisper (whisper.cpp / faster-whisper)

The active backend is selected via ABLE_ASR_PROVIDER env var:
  external  — POST audio to ABLE_ASR_ENDPOINT (default if endpoint is set)
  openai    — OpenAI Whisper API
  local     — Local faster-whisper

When a voice message arrives, the VoiceTranscriber routes to the active
backend and returns a unified TranscriptionResult.
"""

import asyncio
import base64
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


class TranscriptionProvider(Enum):
    EXTERNAL = "external"   # Audio-native model (Voxtral, Qwen3, etc.)
    OPENAI = "openai"       # OpenAI Whisper API
    LOCAL = "local"         # Local Whisper


@dataclass
class TranscriptionResult:
    """Result from transcribing audio"""
    text: str
    language: str
    duration_seconds: float
    segments: List[Dict] = field(default_factory=list)
    provider: TranscriptionProvider = TranscriptionProvider.EXTERNAL
    processing_time_ms: float = 0.0
    confidence: float = 0.0
    metadata: Dict = field(default_factory=dict)


@dataclass
class AudioConfig:
    """Configuration for audio processing"""
    sample_rate: int = 16000
    channels: int = 1
    format: str = "wav"
    max_duration_seconds: int = 600  # 10 minutes max


# ── External ASR Backend ──────────────────────────────────────────────────────


class ExternalASR:
    """
    Send raw audio to an external ASR endpoint.

    Designed for audio-native frontier models (Voxtral, Qwen3, etc.).
    The endpoint receives multipart form data with the audio file and
    returns JSON with at least a "text" field.

    Configure via environment:
        ABLE_ASR_ENDPOINT — Full URL (e.g., http://localhost:8001/v1/transcribe)
        ABLE_ASR_API_KEY  — Bearer token (optional)
        ABLE_ASR_MODEL    — Model identifier sent in the request (optional)

    Expected response format:
        {"text": "transcribed text", "language": "en", "duration": 5.2}
    """

    SUPPORTED_FORMATS = {"mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm", "ogg", "flac"}

    def __init__(
        self,
        endpoint: str = None,
        api_key: str = None,
        model: str = None,
    ):
        self.endpoint = endpoint or os.environ.get("ABLE_ASR_ENDPOINT", "")
        self.api_key = api_key or os.environ.get("ABLE_ASR_API_KEY", "")
        self.model = model or os.environ.get("ABLE_ASR_MODEL", "")
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def is_configured(self) -> bool:
        return bool(self.endpoint)

    async def _get_session(self) -> aiohttp.ClientSession:
        if not AIOHTTP_AVAILABLE:
            raise RuntimeError("aiohttp not installed")
        if self._session is None or self._session.closed:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session

    async def transcribe(
        self,
        audio_data: bytes,
        filename: str = "audio.wav",
        language: str = None,
    ) -> TranscriptionResult:
        if not self.endpoint:
            raise RuntimeError("ABLE_ASR_ENDPOINT not configured")

        start_time = time.time()
        session = await self._get_session()

        data = aiohttp.FormData()
        content_type = "audio/wav"
        ext = Path(filename).suffix.lstrip(".")
        if ext in ("ogg",):
            content_type = "audio/ogg"
        elif ext in ("mp3",):
            content_type = "audio/mpeg"
        elif ext in ("flac",):
            content_type = "audio/flac"
        data.add_field("file", audio_data, filename=filename, content_type=content_type)
        if self.model:
            data.add_field("model", self.model)
        if language:
            data.add_field("language", language)

        try:
            async with session.post(self.endpoint, data=data) as response:
                if response.status != 200:
                    text = await response.text()
                    raise RuntimeError(f"ASR endpoint error {response.status}: {text}")
                result = await response.json()

            elapsed_ms = (time.time() - start_time) * 1000

            return TranscriptionResult(
                text=result.get("text", ""),
                language=result.get("language", "unknown"),
                duration_seconds=result.get("duration", 0.0),
                segments=result.get("segments", []),
                provider=TranscriptionProvider.EXTERNAL,
                processing_time_ms=elapsed_ms,
                metadata={"model": self.model, "endpoint": self.endpoint},
            )
        except Exception as e:
            logger.error(f"External ASR failed: {e}")
            raise

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ── OpenAI Whisper Backend ────────────────────────────────────────────────────


class OpenAIWhisper:
    """
    OpenAI Whisper API for transcription (legacy fallback).

    Supports: mp3, mp4, mpeg, mpga, m4a, wav, webm
    Max file size: 25MB
    """

    API_URL = "https://api.openai.com/v1/audio/transcriptions"
    SUPPORTED_FORMATS = {"mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm", "ogg"}

    def __init__(self, api_key: str = None, model: str = "whisper-1"):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if not AIOHTTP_AVAILABLE:
            raise RuntimeError("aiohttp not installed")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self.api_key}"}
            )
        return self._session

    async def transcribe(
        self,
        audio_data: bytes,
        filename: str = "audio.wav",
        language: str = None,
        prompt: str = None,
        response_format: str = "verbose_json",
    ) -> TranscriptionResult:
        if not self.api_key:
            raise ValueError("OpenAI API key not configured")

        start_time = time.time()
        session = await self._get_session()

        data = aiohttp.FormData()
        data.add_field('file', audio_data, filename=filename, content_type='audio/wav')
        data.add_field('model', self.model)
        data.add_field('response_format', response_format)
        if language:
            data.add_field('language', language)
        if prompt:
            data.add_field('prompt', prompt)

        try:
            async with session.post(self.API_URL, data=data) as response:
                if response.status != 200:
                    text = await response.text()
                    raise RuntimeError(f"Whisper API error {response.status}: {text}")
                result = await response.json()

            elapsed_ms = (time.time() - start_time) * 1000
            return TranscriptionResult(
                text=result.get("text", ""),
                language=result.get("language", "unknown"),
                duration_seconds=result.get("duration", 0.0),
                segments=result.get("segments", []),
                provider=TranscriptionProvider.OPENAI,
                processing_time_ms=elapsed_ms,
                metadata={"model": self.model},
            )
        except Exception as e:
            logger.error(f"Whisper transcription failed: {e}")
            raise

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ── Local Whisper Backend ─────────────────────────────────────────────────────


class LocalWhisper:
    """Local Whisper transcription via faster-whisper."""

    def __init__(self, model_size: str = "base", device: str = "cpu", compute_type: str = "int8"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
            logger.info(f"Loaded Whisper model: {self.model_size}")
        except ImportError:
            raise RuntimeError("faster-whisper not installed. Run: pip install faster-whisper")

    async def transcribe(self, audio_data: bytes, filename: str = "audio.wav", language: str = None) -> TranscriptionResult:
        start_time = time.time()
        self._load_model()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_data)
            temp_path = f.name

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: self._transcribe_file(temp_path, language))
            result.processing_time_ms = (time.time() - start_time) * 1000
            return result
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def _transcribe_file(self, path: str, language: str = None) -> TranscriptionResult:
        segments, info = self._model.transcribe(path, language=language, beam_size=5, vad_filter=True)
        text_parts = []
        segment_list = []
        for segment in segments:
            text_parts.append(segment.text)
            segment_list.append({"start": segment.start, "end": segment.end, "text": segment.text})
        return TranscriptionResult(
            text=" ".join(text_parts),
            language=info.language,
            duration_seconds=info.duration,
            segments=segment_list,
            provider=TranscriptionProvider.LOCAL,
            metadata={"model_size": self.model_size},
        )


# ── Unified Transcriber ──────────────────────────────────────────────────────


class VoiceTranscriber:
    """
    Unified voice transcription with pluggable backends.

    Backend selection (in priority order):
    1. ABLE_ASR_PROVIDER env var (explicit choice)
    2. ABLE_ASR_ENDPOINT set → external
    3. Fallback to openai

    Usage:
        transcriber = VoiceTranscriber()
        result = await transcriber.transcribe(audio_bytes)
        print(result.text)
    """

    def __init__(
        self,
        provider: TranscriptionProvider = None,
        openai_api_key: str = None,
        local_model_size: str = "base",
    ):
        # Auto-detect provider
        if provider is None:
            env_provider = os.environ.get("ABLE_ASR_PROVIDER", "").lower()
            if env_provider == "external":
                provider = TranscriptionProvider.EXTERNAL
            elif env_provider == "local":
                provider = TranscriptionProvider.LOCAL
            elif env_provider == "openai":
                provider = TranscriptionProvider.OPENAI
            elif os.environ.get("ABLE_ASR_ENDPOINT"):
                provider = TranscriptionProvider.EXTERNAL
            else:
                provider = TranscriptionProvider.OPENAI

        self.provider = provider
        self._external = ExternalASR()
        self._openai = OpenAIWhisper(api_key=openai_api_key)
        self._local = LocalWhisper(model_size=local_model_size)

        logger.info(f"VoiceTranscriber initialized: provider={provider.value}")

    async def transcribe(
        self,
        audio_data: bytes,
        filename: str = "audio.wav",
        language: str = None,
        prompt: str = None,
    ) -> TranscriptionResult:
        try:
            if self.provider == TranscriptionProvider.EXTERNAL:
                return await self._external.transcribe(audio_data, filename, language)
            elif self.provider == TranscriptionProvider.LOCAL:
                return await self._local.transcribe(audio_data, filename, language)
            else:
                return await self._openai.transcribe(audio_data, filename, language, prompt)
        except Exception as e:
            logger.error(f"Transcription failed with {self.provider.value}: {e}")
            # Try fallback chain: external → openai → local
            for fallback in [self._external, self._openai, self._local]:
                if fallback is self._get_backend(self.provider):
                    continue
                try:
                    if isinstance(fallback, ExternalASR) and not fallback.is_configured:
                        continue
                    return await fallback.transcribe(audio_data, filename, language)
                except Exception:
                    continue
            raise

    def _get_backend(self, provider: TranscriptionProvider):
        return {
            TranscriptionProvider.EXTERNAL: self._external,
            TranscriptionProvider.OPENAI: self._openai,
            TranscriptionProvider.LOCAL: self._local,
        }[provider]

    async def transcribe_file(self, file_path: Path, language: str = None, prompt: str = None) -> TranscriptionResult:
        file_path = Path(file_path)
        audio_data = file_path.read_bytes()
        return await self.transcribe(audio_data, filename=file_path.name, language=language, prompt=prompt)

    async def close(self):
        await self._openai.close()
        await self._external.close()
