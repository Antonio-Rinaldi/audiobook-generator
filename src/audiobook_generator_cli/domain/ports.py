from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from audiobook_generator_cli.domain.models import AudioRequest, AudioResponse, ChapterDocument


class EpubRepositoryPort(Protocol):
    """Abstraction for reading and writing EPUB books."""

    def load(self, input_path: Path) -> "EpubBook":
        """Load an EPUB from disk into in-memory book representation."""
        raise NotImplementedError

    def save(self, book: "EpubBook", output_path: Path) -> None:
        """Persist an in-memory EPUB representation to disk."""
        raise NotImplementedError


class AudioGeneratorPort(Protocol):
    """Converts text to audio bytes."""

    def generate(self, request: AudioRequest, stream: bool = False) -> AudioResponse:
        """Generate audio for one text request."""
        raise NotImplementedError


@dataclass(frozen=True)
class EpubBook:
    """In-memory EPUB representation used by the application layer.

    `items` maps internal EPUB path -> bytes content.
    `chapters` contains parsed XHTML chapters derived from items.

    Keeping both allows round-trip with minimal loss.
    """

    items: dict[str, bytes]
    chapters: list[ChapterDocument]
