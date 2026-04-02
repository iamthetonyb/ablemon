"""
Voice Transcription - Speech-to-text via Whisper API.

Supports:
- OpenAI Whisper API
- Local Whisper (whisper.cpp or faster-whisper)
- Multiple audio formats

When a voice message arrives, transcribe and process as text.
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
from typing import Any, Dict, List, Optional, BinaryIO

logger = logging.getLogger(__name__)

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


class TranscriptionProvider(Enum):
    OPENAI = "openai"
    LOCAL = "local"  # Local Whisper


@dataclass
class TranscriptionResult:
    """Result from transcribing audio"""
    text: str
    language: str
    duration_seconds: float
    segments: List[Dict] = field(default_factory=list)
    provider: TranscriptionProvider = TranscriptionProvider.OPENAI
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


class OpenAIWhisper:
    """
    OpenAI Whisper API for transcription.

    Supports: mp3, mp4, mpeg, mpga, m4a, wav, webm
    Max file size: 25MB
    """

    API_URL = "https://api.openai.com/v1/audio/transcriptions"
    SUPPORTED_FORMATS = {"mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm", "ogg"}

    def __init__(
        self,
        api_key: str = None,
        model: str = "whisper-1",
    ):
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
        """
        Transcribe audio data.

        Args:
            audio_data: Raw audio bytes
            filename: Original filename (for format detection)
            language: Language code (e.g., "en") or None for auto-detect
            prompt: Optional prompt to guide transcription
            response_format: json, text, srt, verbose_json, vtt

        Returns:
            TranscriptionResult with transcribed text
        """
        if not self.api_key:
            raise ValueError("OpenAI API key not configured")

        start_time = time.time()
        session = await self._get_session()

        # Build form data
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
            logger.error(f"Transcription failed: {e}")
            raise

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


class LocalWhisper:
    """
    Local Whisper transcription via whisper.cpp or faster-whisper.

    Requires: pip install faster-whisper
    """

    def __init__(
        self,
        model_size: str = "base",  # tiny, base, small, medium, large
        device: str = "cpu",
        compute_type: str = "int8",
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _load_model(self):
        """Load the Whisper model"""
        if self._model is not None:
            return

        try:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
            logger.info(f"Loaded Whisper model: {self.model_size}")
        except ImportError:
            raise RuntimeError(
                "faster-whisper not installed. "
                "Run: pip install faster-whisper"
            )

    async def transcribe(
        self,
        audio_data: bytes,
        filename: str = "audio.wav",
        language: str = None,
    ) -> TranscriptionResult:
        """Transcribe audio using local Whisper"""
        start_time = time.time()

        # Load model if needed
        self._load_model()

        # Write to temp file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_data)
            temp_path = f.name

        try:
            # Run transcription in executor (it's CPU-bound)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._transcribe_file(temp_path, language)
            )

            elapsed_ms = (time.time() - start_time) * 1000
            result.processing_time_ms = elapsed_ms
            return result

        finally:
            # Clean up temp file
            Path(temp_path).unlink(missing_ok=True)

    def _transcribe_file(self, path: str, language: str = None) -> TranscriptionResult:
        """Synchronous transcription"""
        segments, info = self._model.transcribe(
            path,
            language=language,
            beam_size=5,
            vad_filter=True,
        )

        # Collect segments
        text_parts = []
        segment_list = []

        for segment in segments:
            text_parts.append(segment.text)
            segment_list.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
            })

        return TranscriptionResult(
            text=" ".join(text_parts),
            language=info.language,
            duration_seconds=info.duration,
            segments=segment_list,
            provider=TranscriptionProvider.LOCAL,
            metadata={"model_size": self.model_size},
        )


class VoiceTranscriber:
    """
    Unified voice transcription with provider fallback.

    Usage:
        transcriber = VoiceTranscriber()
        result = await transcriber.transcribe(audio_bytes)
        print(result.text)
    """

    def __init__(
        self,
        provider: TranscriptionProvider = TranscriptionProvider.OPENAI,
        openai_api_key: str = None,
        local_model_size: str = "base",
    ):
        self.provider = provider

        self._openai = OpenAIWhisper(api_key=openai_api_key)
        self._local = LocalWhisper(model_size=local_model_size)

    async def transcribe(
        self,
        audio_data: bytes,
        filename: str = "audio.wav",
        language: str = None,
        prompt: str = None,
    ) -> TranscriptionResult:
        """
        Transcribe audio to text.

        Args:
            audio_data: Raw audio bytes
            filename: Original filename
            language: Language code or None for auto-detect
            prompt: Optional prompt to guide transcription

        Returns:
            TranscriptionResult
        """
        try:
            if self.provider == TranscriptionProvider.OPENAI:
                return await self._openai.transcribe(
                    audio_data, filename, language, prompt
                )
            else:
                return await self._local.transcribe(
                    audio_data, filename, language
                )

        except Exception as e:
            logger.error(f"Transcription failed with {self.provider.value}: {e}")

            # Try fallback
            if self.provider == TranscriptionProvider.OPENAI:
                logger.info("Falling back to local Whisper")
                try:
                    return await self._local.transcribe(audio_data, filename, language)
                except Exception:
                    pass

            raise

    async def transcribe_file(
        self,
        file_path: Path,
        language: str = None,
        prompt: str = None,
    ) -> TranscriptionResult:
        """Transcribe an audio file"""
        file_path = Path(file_path)
        audio_data = file_path.read_bytes()
        return await self.transcribe(
            audio_data,
            filename=file_path.name,
            language=language,
            prompt=prompt,
        )

    async def close(self):
        """Close provider connections"""
        await self._openai.close()
