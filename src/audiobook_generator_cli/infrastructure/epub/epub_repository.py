from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from audiobook_generator_cli.domain.errors import EpubReadError
from audiobook_generator_cli.domain.models import ChapterDocument
from audiobook_generator_cli.domain.ports import EpubBook, EpubRepositoryPort
from audiobook_generator_cli.infrastructure.logging.logger_factory import create_logger

logger = create_logger(__name__)


@dataclass(frozen=True)
class ZipEpubRepository(EpubRepositoryPort):
    """Load EPUBs from zip archives.

    EPUB is a zip container with specific constraints (mimetype must be first
    and stored). This implementation preserves all non-chapter items byte-for-byte.
    """

    @staticmethod
    def _read_items(input_path: Path) -> dict[str, bytes]:
        """Read all ZIP entries from EPUB file into memory."""
        with zipfile.ZipFile(input_path, "r") as zip_file:
            return {name: zip_file.read(name) for name in zip_file.namelist()}

    @staticmethod
    def _is_chapter_resource(resource_path: str) -> bool:
        """Return True when resource path appears to contain chapter markup."""
        return resource_path.lower().endswith((".xhtml", ".html", ".htm"))

    @classmethod
    def _chapter_documents(cls, items: dict[str, bytes]) -> list[ChapterDocument]:
        """Convert EPUB resources into ordered chapter document payloads."""
        return [
            ChapterDocument(path=resource_path, xhtml_bytes=content)
            for resource_path, content in items.items()
            if cls._is_chapter_resource(resource_path)
        ]

    def load(self, input_path: Path) -> EpubBook:
        """Load EPUB file from disk and return in-memory chapter-aware representation."""
        try:
            items = self._read_items(input_path)
        except Exception as exc:  # noqa: BLE001
            raise EpubReadError(str(exc)) from exc

        chapters = self._chapter_documents(items)

        logger.debug(
            "EPUB repository load completed | items=%s chapters=%s",
            len(items),
            len(chapters),
        )
        return EpubBook(items=items, chapters=chapters)