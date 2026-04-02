from __future__ import annotations

import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from threading import Lock

from pydub import AudioSegment

from lxml import etree

from audiobook_generator_cli.domain.models import AudioRequest, AudioSettings
from audiobook_generator_cli.domain.ports import AudioGeneratorPort, EpubRepositoryPort
from audiobook_generator_cli.infrastructure.logging.logger_factory import create_logger

logger = create_logger(__name__)

# Tags whose text content we want to extract for narration.
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_NARRATABLE_TAGS = _HEADING_TAGS | {"p", "li", "blockquote", "dd", "dt", "figcaption", "td", "th"}


_WS_RE = re.compile(r"\s+")
_PROGRESS_INDEX_FILE = ".audiobook_progress.json"
_TEMP_CHUNKS_DIR = ".audio_chuncks"
_CHAPTER_XML_DIR = ".chapters"

_BASE_READING_INSTRUCTION = (
    "Maintain a consistent tone and stable volume across the entire passage. "
    "Strictly follow punctuation for natural narration: apply clear pauses at commas, full stops, semicolons, colons, question marks, and exclamation marks."
)
_HEADING_READING_INSTRUCTION = (
    "Read headings calmly and clearly, without shouting or abrupt emphasis."
)


def _local_tag_name(tag: str | None) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _normalise_block_text(elem: etree._Element) -> str:
    """Extract visible text from an XHTML block, preserving nested inline content."""
    raw = " ".join(text for text in elem.itertext())
    cleaned = _WS_RE.sub(" ", raw).strip()
    # Avoid artifacts like "word !" after itertext normalization.
    return re.sub(r"\s+([,.;:!?…])", r"\1", cleaned)


def _has_spoken_text(text: str) -> bool:
    # Keep any block with letters/digits from any alphabet, skip punctuation-only placeholders.
    return any(ch.isalnum() for ch in text)


def _is_heading_tag(tag: str) -> bool:
    return _local_tag_name(tag) in _HEADING_TAGS


@dataclass(frozen=True)
class NarrationBlock:
    tag: str
    text: str

    @property
    def is_heading(self) -> bool:
        return self.tag in _HEADING_TAGS


def _extract_narration_blocks(xhtml_bytes: bytes) -> list[NarrationBlock]:
    """Extract narratable blocks preserving tag type for style/pause decisions."""
    try:
        root = etree.fromstring(xhtml_bytes)
    except etree.XMLSyntaxError:
        logger.error("XMLSyntaxError in _extract_paragraphs")
        return []

    blocks: list[NarrationBlock] = []
    for elem in root.iter():
        local_tag = _local_tag_name(elem.tag)
        if local_tag not in _NARRATABLE_TAGS:
            logger.debug("Skipping non-narratable element | tag=%s", elem.tag)
            continue

        cleaned = _normalise_block_text(elem)
        if not cleaned or not _has_spoken_text(cleaned):
            logger.debug("Skipping non-narratable element | tag=%s cleaned=%r", elem.tag, cleaned)
            continue

        if _is_heading_tag(elem.tag) and cleaned[-1] not in ".!?…:":
            logger.debug("Appending period to heading | tag=%s cleaned=%r", elem.tag, cleaned)
            cleaned = f"{cleaned}."

        blocks.append(NarrationBlock(tag=local_tag, text=cleaned))
    return blocks


def _extract_paragraphs(xhtml_bytes: bytes) -> list[str]:
    """Backward-compatible helper used by tests."""
    return [block.text for block in _extract_narration_blocks(xhtml_bytes)]


def _chapter_tmp_dir(audiobook_dir: Path, chapter_index: int, chapter_path: str) -> Path:
    stem = Path(chapter_path).stem
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]", "_", stem)
    return audiobook_dir / _TEMP_CHUNKS_DIR / f"chapter_{chapter_index}_{safe_stem}"


def _chunk_path_for_index(chapter_tmp_dir: Path, paragraph_index: int) -> Path | None:
    matches = sorted(chapter_tmp_dir.glob(f"chunk_{paragraph_index}.*"))
    return matches[0] if matches else None


def _count_contiguous_existing_chunks(chapter_tmp_dir: Path, total_blocks: int) -> int:
    count = 0
    for paragraph_index in range(1, total_blocks + 1):
        if _chunk_path_for_index(chapter_tmp_dir, paragraph_index) is None:
            break
        count = paragraph_index
    return count


@dataclass
class ProgressIndex:
    path: Path
    lock: Lock

    def _load_unlocked(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "chapters": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Progress index is invalid JSON, resetting | path=%s", self.path)
            return {"version": 1, "chapters": {}}
        if not isinstance(payload, dict):
            return {"version": 1, "chapters": {}}
        chapters = payload.get("chapters")
        if not isinstance(chapters, dict):
            payload["chapters"] = {}
        return payload

    def _save_unlocked(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(self.path)

    def get_chapter(self, chapter_key: str) -> dict:
        with self.lock:
            payload = self._load_unlocked()
            chapter_state = payload.get("chapters", {}).get(chapter_key)
            if not isinstance(chapter_state, dict):
                return {}
            return chapter_state.copy()

    def upsert_chapter_progress(
        self,
        chapter_key: str,
        chapter_path: str,
        total_blocks: int,
        completed_blocks: int,
        output_file: str,
        completed: bool,
    ) -> None:
        with self.lock:
            payload = self._load_unlocked()
            chapters = payload.setdefault("chapters", {})
            chapters[chapter_key] = {
                "chapter_path": chapter_path,
                "total_blocks": total_blocks,
                "completed_blocks": completed_blocks,
                "completed": completed,
                "output_file": output_file,
            }
            self._save_unlocked(payload)


def _instruction_for_block(block: NarrationBlock, settings: AudioSettings) -> str:
    parts = [_BASE_READING_INSTRUCTION]
    if block.is_heading:
        parts.append(_HEADING_READING_INSTRUCTION)
        if settings.heading_tone:
            parts.append(settings.heading_tone)
    elif settings.paragraph_tone:
        parts.append(settings.paragraph_tone)
    return "\n".join(parts)


def _merge_audio_chunks(
    blocks: list[NarrationBlock],
    audio_chunks: list[bytes],
    out_file: Path,
    paragraph_pause_ms: int,
    output_format: str,
) -> None:
    combined = AudioSegment.empty()
    for idx, audio_bytes in enumerate(audio_chunks):
        combined += AudioSegment.from_file(BytesIO(audio_bytes))
        is_last = idx == len(audio_chunks) - 1
        if is_last:
            continue
        if (not blocks[idx].is_heading) and (not blocks[idx + 1].is_heading) and paragraph_pause_ms > 0:
            combined += AudioSegment.silent(duration=paragraph_pause_ms)
    combined.export(out_file, format=output_format)


def _merge_temp_chunks(
    block_audio_files: list[tuple[NarrationBlock, Path]],
    out_file: Path,
    paragraph_pause_ms: int,
    output_format: str,
) -> None:
    combined = AudioSegment.empty()
    for idx, (block, audio_path) in enumerate(block_audio_files):
        combined += AudioSegment.from_file(audio_path)
        is_last = idx == len(block_audio_files) - 1
        if is_last:
            continue
        next_block = block_audio_files[idx + 1][0]
        if (not block.is_heading) and (not next_block.is_heading) and paragraph_pause_ms > 0:
            combined += AudioSegment.silent(duration=paragraph_pause_ms)
    combined.export(out_file, format=output_format)

@dataclass(frozen=True)
class AudiobookOrchestrator:
    """Generate a folder of per-chapter audio files from an EPUB.

    This orchestrator is independent of any translation pipeline: it uses its
    own ``AudioGeneratorPort`` (backed by a voice model) and writes
    ``<audiobook_dir>/<chapter_stem>.<format>`` for every non-empty chapter.
    """

    epub_repository: EpubRepositoryPort
    audio_generator: AudioGeneratorPort

    @staticmethod
    def _reset_progress_state(audiobook_dir: Path) -> None:
        index_path = audiobook_dir / _PROGRESS_INDEX_FILE
        temp_chunks_dir = audiobook_dir / _TEMP_CHUNKS_DIR

        if index_path.exists():
            index_path.unlink(missing_ok=True)
            logger.info("Removed progress index | path=%s", index_path)

        if temp_chunks_dir.exists():
            shutil.rmtree(temp_chunks_dir, ignore_errors=True)
            logger.info("Removed temp chunk directory | path=%s", temp_chunks_dir)

    def _process_chapter(
            self,
            i: int,
            total: int,
            chapter,
            audiobook_dir: Path,
            settings: AudioSettings,
            progress: ProgressIndex,
            stream: bool = False) -> bool:

        audiobook_dir.mkdir(parents=True, exist_ok=True)
        chapters_dir = audiobook_dir / _CHAPTER_XML_DIR
        chapters_dir.mkdir(parents=True, exist_ok=True)
        chapter_xhtml_bytes = chapter.xhtml_bytes
        with open(f"{chapters_dir}/chapter_{i}.xml", mode="wb") as chapter_xml:
            chapter_xml.write(chapter_xhtml_bytes)

        blocks = _extract_narration_blocks(chapter_xhtml_bytes)
        if not blocks:
            logger.info("Skipping empty chapter %s/%s | path=%s", i, total, chapter.path)
            return False

        chapter_key = chapter.path
        stem = Path(chapter.path).stem
        output_format = settings.chapter_format.strip().lower() or "wav"
        out_file = audiobook_dir / f"{stem}.{output_format}"

        chapter_state = progress.get_chapter(chapter_key)
        if chapter_state.get("completed") and out_file.exists():
            logger.info("Skipping already completed chapter %s/%s | path=%s", i, total, chapter.path)
            return True

        logger.info(
            "Generating audio %s/%s | chapter=%s paragraphs=%s",
            i,
            total,
            chapter.path,
            len(blocks),
        )
        try:
            temp_dir_path = _chapter_tmp_dir(audiobook_dir, i, chapter.path)
            temp_dir_path.mkdir(parents=True, exist_ok=True)

            existing_chunks = _count_contiguous_existing_chunks(temp_dir_path, len(blocks))
            completed_blocks = int(chapter_state.get("completed_blocks", 0) or 0)
            completed_blocks = max(0, min(len(blocks), max(completed_blocks, existing_chunks)))

            if completed_blocks > 0:
                logger.info(
                    "Resuming chapter %s/%s from paragraph %s/%s | chapter=%s",
                    i,
                    total,
                    completed_blocks + 1,
                    len(blocks),
                    chapter.path,
                )

            progress.upsert_chapter_progress(
                chapter_key=chapter_key,
                chapter_path=chapter.path,
                total_blocks=len(blocks),
                completed_blocks=completed_blocks,
                output_file=str(out_file),
                completed=False,
            )

            for paragraph_index in range(completed_blocks + 1, len(blocks) + 1):
                block = blocks[paragraph_index - 1]
                request = AudioRequest(
                    model=settings.model,
                    text=block.text,
                    voice=settings.voice,
                    instructions=_instruction_for_block(block, settings),
                )
                response = self.audio_generator.generate(request, stream=stream)
                chunk_path = temp_dir_path / f"chunk_{paragraph_index}.{response.format}"
                chunk_path.write_bytes(response.audio_bytes)
                completed_blocks = paragraph_index
                progress.upsert_chapter_progress(
                    chapter_key=chapter_key,
                    chapter_path=chapter.path,
                    total_blocks=len(blocks),
                    completed_blocks=completed_blocks,
                    output_file=str(out_file),
                    completed=False,
                )

            block_audio_files: list[tuple[NarrationBlock, Path]] = []
            for paragraph_index, block in enumerate(blocks, start=1):
                chunk_path = _chunk_path_for_index(temp_dir_path, paragraph_index)
                if chunk_path is None:
                    raise RuntimeError(f"Missing chunk for paragraph {paragraph_index}")
                block_audio_files.append((block, chunk_path))

            _merge_temp_chunks(
                block_audio_files=block_audio_files,
                out_file=out_file,
                paragraph_pause_ms=settings.paragraph_pause_ms,
                output_format=output_format,
            )
            shutil.rmtree(temp_dir_path, ignore_errors=True)

            progress.upsert_chapter_progress(
                chapter_key=chapter_key,
                chapter_path=chapter.path,
                total_blocks=len(blocks),
                completed_blocks=len(blocks),
                output_file=str(out_file),
                completed=True,
            )

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
        reset_progress: bool = False,
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
        audiobook_dir.mkdir(parents=True, exist_ok=True)
        if reset_progress:
            self._reset_progress_state(audiobook_dir)

        if workers > 1:
            logger.warning(
                "Resume index writes are serialized; paragraph-level checkpoints are more deterministic with --workers 1"
            )
        book = self.epub_repository.load(translated_epub_path)
        total = len(book.chapters)
        progress = ProgressIndex(path=audiobook_dir / _PROGRESS_INDEX_FILE, lock=Lock())
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = (
                executor.submit(
                    self._process_chapter,
                    i + 1,
                    total,
                    chapter,
                    audiobook_dir,
                    settings,
                    progress,
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

