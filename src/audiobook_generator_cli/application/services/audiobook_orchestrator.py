from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from audiobook_generator_cli.domain.models import AudioRequest, AudioSettings
from audiobook_generator_cli.domain.ports import AudioGeneratorPort, EpubRepositoryPort
from audiobook_generator_cli.infrastructure.logging.logger_factory import create_logger

logger = create_logger(__name__)

# XHTML namespace used in EPUB spine files.
_XHTML_NS = "http://www.w3.org/1999/xhtml"

# Tags whose text content we want to extract for narration.
_NARRATE_TAGS = frozenset(
    {
        f"{{{_XHTML_NS}}}p",
        f"{{{_XHTML_NS}}}h1",
        f"{{{_XHTML_NS}}}h2",
        f"{{{_XHTML_NS}}}h3",
        f"{{{_XHTML_NS}}}h4",
        f"{{{_XHTML_NS}}}h5",
        f"{{{_XHTML_NS}}}h6",
        # Also match un-namespaced variants produced by some parsers.
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
)

_WS_RE = re.compile(r"\s+")


def _normalise_block_text(elem: etree._Element) -> str:
    """Extract visible text from an XHTML block, preserving inline tails."""
    parts: list[str] = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        if child.text:
            parts.append(child.text)
        if child.tail:
            parts.append(child.tail)
    raw = " ".join(parts)
    return _WS_RE.sub(" ", raw).strip()


def _is_heading_tag(tag: str) -> bool:
    return tag in {
        f"{{{_XHTML_NS}}}h1",
        f"{{{_XHTML_NS}}}h2",
        f"{{{_XHTML_NS}}}h3",
        f"{{{_XHTML_NS}}}h4",
        f"{{{_XHTML_NS}}}h5",
        f"{{{_XHTML_NS}}}h6",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }


def _extract_text(xhtml_bytes: bytes) -> str:
    """Return chapter text with title/paragraph separators to improve TTS prosody."""
    try:
        root = etree.fromstring(xhtml_bytes)
    except etree.XMLSyntaxError:
        return ""

    blocks: list[str] = []
    for elem in root.iter():
        if elem.tag not in _NARRATE_TAGS:
            continue

        cleaned = _normalise_block_text(elem)
        if not cleaned:
            continue

        if _is_heading_tag(elem.tag) and cleaned[-1] not in ".!?…:":
            # Encourage a natural stop after titles/headings.
            cleaned = f"{cleaned}."

        blocks.append(cleaned)

    # Double newline gives stronger pause between sections and paragraphs.
    return "\n\n".join(blocks)


@dataclass(frozen=True)
class AudiobookOrchestrator:
    """Generate a folder of per-chapter audio files from an EPUB.

    This orchestrator is independent of any translation pipeline: it uses its
    own ``AudioGeneratorPort`` (backed by a voice model) and writes
    ``<audiobook_dir>/<chapter_stem>.<format>`` for every non-empty chapter.
    """

    epub_repository: EpubRepositoryPort
    audio_generator: AudioGeneratorPort

    def generate(
        self,
        translated_epub_path: Path,
        audiobook_dir: Path,
        settings: AudioSettings,
    ) -> int:
        """Generate audio for every chapter.

        Returns the number of chapters successfully written.
        """
        logger.info(
            "Loading EPUB for audiobook | path=%s model=%s",
            translated_epub_path,
            settings.model,
        )
        book = self.epub_repository.load(translated_epub_path)
        audiobook_dir.mkdir(parents=True, exist_ok=True)

        written = 0
        total = len(book.chapters)

        for i, chapter in enumerate(book.chapters, start=1):
            chapter_xhtml_bytes = chapter.xhtml_bytes
            chapters_dir = audiobook_dir / "chapters"
            chapters_dir.mkdir(parents=True, exist_ok=True)
            with open(f"{chapters_dir}/chapter_{i}.xml", mode="wb") as chapter_xml:
                chapter_xml.write(chapter_xhtml_bytes)

            text = _extract_text(chapter_xhtml_bytes)
            if not text.strip():
                logger.info("Skipping empty chapter %s/%s | path=%s", i, total, chapter.path)
                continue

            stem = Path(chapter.path).stem

            logger.info(
                "Generating audio %s/%s | chapter=%s chars=%s",
                i,
                total,
                chapter.path,
                len(text),
            )

            try:
                response = self.audio_generator.generate(
                    AudioRequest(model=settings.model, text=text, voice=settings.voice)
                )
                out_file = audiobook_dir / f"{stem}.{response.format}"
                out_file.write_bytes(response.audio_bytes)
                written += 1
                logger.debug("Audio written | path=%s bytes=%s", out_file, len(response.audio_bytes))
            except Exception as exc:  # noqa: BLE001
                logger.error("Audio generation failed | chapter=%s error=%s", chapter.path, exc)

        logger.info(
            "Audiobook generation complete | written=%s/%s dir=%s",
            written,
            total,
            audiobook_dir,
        )
        return written
