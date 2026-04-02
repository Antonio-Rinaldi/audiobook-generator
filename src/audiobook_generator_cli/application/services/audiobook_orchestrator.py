from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from io import BytesIO
from pydub import AudioSegment

from lxml import etree

from audiobook_generator_cli.domain.models import AudioRequest, AudioSettings
from audiobook_generator_cli.domain.ports import AudioGeneratorPort, EpubRepositoryPort
from audiobook_generator_cli.infrastructure.logging.logger_factory import create_logger

logger = create_logger(__name__)

# Tags whose text content we want to extract for narration.
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_NARRATABLE_TAGS = _HEADING_TAGS | {"p"}


_WS_RE = re.compile(r"\s+")


def _local_tag_name(tag: str | None) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _normalise_block_text(elem: etree._Element) -> str:
    """Extract visible text from an XHTML block, preserving nested inline content."""
    raw = " ".join(text for text in elem.itertext())
    return _WS_RE.sub(" ", raw).strip()


def _is_heading_tag(tag: str) -> bool:
    return _local_tag_name(tag) in _HEADING_TAGS


def _extract_paragraphs(xhtml_bytes: bytes) -> list[str]:
    """Extract each narratable block (paragraph, heading) as a separate string."""
    try:
        root = etree.fromstring(xhtml_bytes)
    except etree.XMLSyntaxError:
        logger.error("XMLSyntaxError in _extract_paragraphs")
        return []

    paragraphs: list[str] = []
    for elem in root.iter():
        local_tag = _local_tag_name(elem.tag)
        if local_tag not in _NARRATABLE_TAGS:
            logger.debug("Skipping non-narratable element | tag=%s", elem.tag)
            continue

        cleaned = _normalise_block_text(elem)
        # Skip if no word character (at least one word)
        if not cleaned or not re.search(r"\w", cleaned):
            logger.debug("Skipping non-narratable element | tag=%s cleaned=%r", elem.tag, cleaned)
            continue

        if _is_heading_tag(elem.tag) and cleaned[-1] not in ".!?…:":
            logger.debug("Appending period to heading | tag=%s cleaned=%r", elem.tag, cleaned)
            cleaned = f"{cleaned}."

        paragraphs.append(cleaned)
    return paragraphs

@dataclass(frozen=True)
class AudiobookOrchestrator:
    """Generate a folder of per-chapter audio files from an EPUB.

    This orchestrator is independent of any translation pipeline: it uses its
    own ``AudioGeneratorPort`` (backed by a voice model) and writes
    ``<audiobook_dir>/<chapter_stem>.<format>`` for every non-empty chapter.
    """

    epub_repository: EpubRepositoryPort
    audio_generator: AudioGeneratorPort

    def _process_chapter(
            self,
            i: int,
            total: int,
            chapter,
            audiobook_dir: Path,
            settings: AudioSettings,
            stream: bool = False) -> bool:

        audiobook_dir.mkdir(parents=True, exist_ok=True)
        chapters_dir = audiobook_dir / "chapters"
        chapters_dir.mkdir(parents=True, exist_ok=True)
        chapter_xhtml_bytes = chapter.xhtml_bytes
        with open(f"{chapters_dir}/chapter_{i}.xml", mode="wb") as chapter_xml:
            chapter_xml.write(chapter_xhtml_bytes)

        paragraphs = _extract_paragraphs(chapter_xhtml_bytes)
        if not paragraphs:
            logger.info("Skipping empty chapter %s/%s | path=%s", i, total, chapter.path)
            return False
        stem = Path(chapter.path).stem
        logger.info(
            "Generating audio %s/%s | chapter=%s paragraphs=%s",
            i,
            total,
            chapter.path,
            len(paragraphs),
        )
        try:
            audio_requests = (
                AudioRequest(model=settings.model, text=para, voice=settings.voice)
                for idx, para in enumerate(paragraphs, start=1)
                if para.strip()
            )
            audio_bytes_list = [
                self.audio_generator.generate(request, stream=stream).audio_bytes
                for request in audio_requests
            ]
            if audio_bytes_list:
                audio_segment_generator = (
                    AudioSegment.from_file(BytesIO(audio_bytes))
                    for audio_bytes in audio_bytes_list
                )
                combined = sum(audio_segment_generator, AudioSegment.empty())
                out_file = audiobook_dir / f"{stem}.mp3"
                combined.export(out_file, format="mp3")
                logger.debug("Audio written | path=%s bytes=%s", out_file, out_file.stat().st_size)
                return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Audio generation failed | chapter=%s error=%s", chapter.path, exc)
        return False

    def generate(
        self,
        translated_epub_path: Path,
        audiobook_dir: Path,
        settings: AudioSettings,
        workers: int = 1,
        stream: bool = False,
    ) -> int:
        """Generate audio for every chapter, processing each paragraph individually.
        Chapters are processed in parallel, but a single chapter is processed by one worker.
        """
        logger.info(
            "Loading EPUB for audiobook | path=%s model=%s workers=%s",
            translated_epub_path,
            settings.model,
            workers,
        )
        book = self.epub_repository.load(translated_epub_path)
        total = len(book.chapters)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = (
                executor.submit(
                    self._process_chapter,
                    i + 1,
                    total,
                    chapter,
                    audiobook_dir,
                    settings,
                    stream
                )
                for i, chapter in enumerate(book.chapters))
            written = sum(1 for future in as_completed(futures) if future.result())

            logger.info(
                "Audiobook generation complete | written=%s/%s dir=%s",
                written,
                total,
                audiobook_dir,
            )
            return written

