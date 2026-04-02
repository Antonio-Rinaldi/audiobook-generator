from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChapterDocument:
    path: str
    xhtml_bytes: bytes


@dataclass(frozen=True)
class AudioSettings:
    """Settings for audiobook generation."""

    model: str
    base_url: str = "http://localhost:11434"
    voice: str = ""
    heading_tone: str = ""
    paragraph_tone: str = ""
    paragraph_pause_ms: int = 700
    spool_temp_chunks: bool = True
    chapter_format: str = "wav"


@dataclass(frozen=True)
class AudioRequest:
    """Ask the voice model to read *text* aloud and return raw audio bytes."""

    model: str
    text: str
    voice: str = ""
    instructions: str = ""


@dataclass(frozen=True)
class AudioResponse:
    """Raw audio bytes returned by the voice generator."""

    audio_bytes: bytes
    format: str = "wav"
