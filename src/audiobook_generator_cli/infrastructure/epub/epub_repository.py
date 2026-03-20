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

    def load(self, input_path: Path) -> EpubBook:
        try:
            with zipfile.ZipFile(input_path, "r") as zf:
                items: dict[str, bytes] = {name: zf.read(name) for name in zf.namelist()}
        except Exception as exc:  # noqa: BLE001
            raise EpubReadError(str(exc)) from exc

        chapters: list[ChapterDocument] = []
        for name, content in items.items():
            lowered = name.lower()
            if lowered.endswith((".xhtml", ".html", ".htm")):
                # Basic heuristic: treat HTML/XHTML as chapters.
                chapters.append(ChapterDocument(path=name, xhtml_bytes=content))

        logger.debug("EPUB repository load completed | items=%s chapters=%s", len(items), len(chapters))
        return EpubBook(items=items, chapters=chapters)

    def save(self, book: EpubBook, output_path: Path) -> None:
        try:
            with zipfile.ZipFile(output_path, "w") as zf:
                # Per EPUB spec, 'mimetype' should be the first entry and stored (no compression).
                if "mimetype" in book.items:
                    zf.writestr(
                        "mimetype",
                        book.items["mimetype"],
                        compress_type=zipfile.ZIP_STORED,
                    )

                for name, content in book.items.items():
                    if name == "mimetype":
                        continue
                    zf.writestr(name, content, compress_type=zipfile.ZIP_DEFLATED)
        except Exception as exc:  # noqa: BLE001
            raise EpubWriteError(str(exc)) from exc

        logger.debug("EPUB repository save completed | items=%s path=%s", len(book.items), output_path)
