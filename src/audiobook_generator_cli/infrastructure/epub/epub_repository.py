from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from audiobook_generator_cli.domain.errors import EpubReadError, EpubWriteError
from audiobook_generator_cli.domain.models import ChapterDocument
from audiobook_generator_cli.domain.ports import EpubBook, EpubRepositoryPort
from audiobook_generator_cli.infrastructure.logging.logger_factory import create_logger


logger = create_logger(__name__)


@dataclass(frozen=True)
class ZipEpubRepository(EpubRepositoryPort):
    """Load and save EPUBs as zip archives.

    Notes:
    - EPUB is a zip container with specific constraints (mimetype must be first and stored).
    - This implementation preserves all non-chapter items byte-for-byte.
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

    @staticmethod
    def _write_items(book: EpubBook, output_path: Path) -> None:
        """Write EPUB resources while preserving ``mimetype`` ordering rules."""
        with zipfile.ZipFile(output_path, "w") as zip_file:
            if "mimetype" in book.items:
                zip_file.writestr(
                    "mimetype",
                    book.items["mimetype"],
                    compress_type=zipfile.ZIP_STORED,
                )

            for name, content in book.items.items():
                if name == "mimetype":
                    continue
                zip_file.writestr(name, content, compress_type=zipfile.ZIP_DEFLATED)

    def load(self, input_path: Path) -> EpubBook:
        """Load EPUB file from disk and return in-memory chapter-aware representation."""
        try:
            items = self._read_items(input_path)
        except Exception as exc:  # noqa: BLE001
            raise EpubReadError(str(exc)) from exc

        chapters = self._chapter_documents(items)

        logger.debug("EPUB repository load completed | items=%s chapters=%s", len(items), len(chapters))
        return EpubBook(items=items, chapters=chapters)

    def save(self, book: EpubBook, output_path: Path) -> None:
        """Persist in-memory EPUB representation to archive file on disk."""
        try:
            self._write_items(book, output_path)
        except Exception as exc:  # noqa: BLE001
            raise EpubWriteError(str(exc)) from exc

        logger.debug("EPUB repository save completed | items=%s path=%s", len(book.items), output_path)
