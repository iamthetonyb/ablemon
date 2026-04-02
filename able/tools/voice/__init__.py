"""
Voice Tools

Speech-to-text transcription using OpenAI Whisper and local models.
"""

from .transcription import (
    VoiceTranscriber,
    TranscriptionResult,
    TranscriptionSegment,
    WhisperModel,
)

__all__ = [
    "VoiceTranscriber",
    "TranscriptionResult",
    "TranscriptionSegment",
    "WhisperModel",
]
