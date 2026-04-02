from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from audiobook_generator_cli.domain.models import AudioRequest, AudioResponse, ChapterDocument


class EpubRepositoryPort(Protocol):
    def load(self, input_path: Path) -> "EpubBook":
        raise NotImplementedError

    def save(self, book: "EpubBook", output_path: Path) -> None:
        raise NotImplementedError


class AudioGeneratorPort(Protocol):
    """Converts text to audio bytes."""

    def generate(self, request: AudioRequest, stream: bool = False) -> AudioResponse:
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
