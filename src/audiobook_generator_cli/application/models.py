from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from audiobook_generator_cli.application.progress import ProgressIndex
from audiobook_generator_cli.domain.models import AudioSettings, ChapterDocument

_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

_TEMP_CHUNKS_DIR = ".audio_chunks"


def _chapter_tmp_dir(audiobook_dir: Path, chapter_index: int, chapter_path: str) -> Path:
    """Return deterministic temporary directory path for one chapter."""
    stem = Path(chapter_path).stem
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]", "_", stem)
    return audiobook_dir / _TEMP_CHUNKS_DIR / f"chapter_{chapter_index}_{safe_stem}"


@dataclass(frozen=True)
class NarrationBlock:
    """Narratable block preserving source tag and cleaned display text."""

    tag: str
    text: str

    @property
    def is_heading(self) -> bool:
        """True when this block originates from a heading tag."""
        return self.tag in _HEADING_TAGS


@dataclass(frozen=True)
class ChapterJob:
    """Immutable execution context for one chapter synthesis task."""

    index: int
    total: int
    chapter: ChapterDocument
    audiobook_dir: Path
    settings: AudioSettings
    progress: ProgressIndex
    stream: bool

    @property
    def label(self) -> str:
        """Human-readable chapter label used for structured logs."""
        return f"{self.index}/{self.total} {self.chapter.path}"

    @property
    def chapter_key(self) -> str:
        """Stable chapter key used in resume index map."""
        return self.chapter.path

    @property
    def output_format(self) -> str:
        """Normalized chapter output format resolved from settings."""
        return self.settings.chapter_format.strip().lower() or "wav"

    @property
    def output_path(self) -> Path:
        """Final chapter output path for merged audio, prefixed with zero-padded index."""
        stem = Path(self.chapter.path).stem
        return self.audiobook_dir / f"{self.index:03d}_{stem}.{self.output_format}"

    @property
    def temp_dir(self) -> Path:
        """Temporary directory used for per-paragraph chunk files."""
        return _chapter_tmp_dir(self.audiobook_dir, self.index, self.chapter.path)


@dataclass(frozen=True)
class PreparedChapter:
    """Prepared chapter data required by the synthesis and merge pipeline."""

    job: ChapterJob
    blocks: list[NarrationBlock]
    completed_blocks: int
    existing_chunks: int