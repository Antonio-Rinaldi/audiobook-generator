from __future__ import annotations

import html
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
_INLINE_TAG_RE = re.compile(r"</?[^>]+>")
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


def _strip_inline_tags_for_tts(text: str) -> str:
    """Remove inline XML/HTML markers from text before TTS synthesis."""
    unescaped = html.unescape(text)
    without_tags = _INLINE_TAG_RE.sub(" ", unescaped)
    collapsed = _WS_RE.sub(" ", without_tags).strip()
    return re.sub(r"\s+([,.;:!?…])", r"\1", collapsed)


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

    all_elements = list(root.iter())
    narratable_elements = [elem for elem in all_elements if _local_tag_name(elem.tag) in _NARRATABLE_TAGS]

    cleaned_by_elem = {
        elem: _normalise_block_text(elem)
        for elem in narratable_elements
    }
    spoken_elements = [
        elem
        for elem in narratable_elements
        if cleaned_by_elem[elem] and _has_spoken_text(cleaned_by_elem[elem])
    ]
    heading_period_appended = sum(
        1
        for elem in spoken_elements
        if _is_heading_tag(elem.tag) and cleaned_by_elem[elem][-1] not in ".!?…:"
    )

    blocks = [
        NarrationBlock(
            tag=_local_tag_name(elem.tag),
            text=(
                f"{cleaned_by_elem[elem]}."
                if _is_heading_tag(elem.tag) and cleaned_by_elem[elem][-1] not in ".!?…:"
                else cleaned_by_elem[elem]
            ),
        )
        for elem in spoken_elements
    ]

    logger.debug(
        "Extraction summary | narratable=%s skipped_non_narratable=%s skipped_empty=%s heading_period_appended=%s",
        len(blocks),
        len(all_elements) - len(narratable_elements),
        len(narratable_elements) - len(spoken_elements),
        heading_period_appended,
    )
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


def _pending_block_indexes(completed_blocks: int, total_blocks: int) -> range:
    return range(completed_blocks + 1, total_blocks + 1)

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
        (chapters_dir / f"chapter_{i}.xml").write_bytes(chapter_xhtml_bytes)

        chapter_label = f"{i}/{total} {chapter.path}"
        blocks = _extract_narration_blocks(chapter_xhtml_bytes)
        if not blocks:
            logger.info("Chapter %s | skipped (no narratable blocks)", chapter_label)
            return False

        chapter_key = chapter.path
        stem = Path(chapter.path).stem
        output_format = settings.chapter_format.strip().lower() or "wav"
        out_file = audiobook_dir / f"{stem}.{output_format}"

        chapter_state = progress.get_chapter(chapter_key)
        if chapter_state.get("completed") and out_file.exists():
            logger.info("Chapter %s | already completed, skipping", chapter_label)
            return True

        logger.info(
            "Chapter %s | start | blocks=%s output=%s",
            chapter_label,
            len(blocks),
            out_file,
        )
        try:
            temp_dir_path = _chapter_tmp_dir(audiobook_dir, i, chapter.path)
            temp_dir_path.mkdir(parents=True, exist_ok=True)

            existing_chunks = _count_contiguous_existing_chunks(temp_dir_path, len(blocks))
            completed_blocks = int(chapter_state.get("completed_blocks", 0) or 0)
            completed_blocks = max(0, min(len(blocks), max(completed_blocks, existing_chunks)))

            if completed_blocks > 0:
                logger.info(
                    "Chapter %s | resume from chunk %s/%s",
                    chapter_label,
                    completed_blocks + 1,
                    len(blocks),
                )

            logger.debug(
                "Chapter %s | temp_dir=%s existing_chunks=%s",
                chapter_label,
                temp_dir_path,
                existing_chunks,
            )

            progress.upsert_chapter_progress(
                chapter_key=chapter_key,
                chapter_path=chapter.path,
                total_blocks=len(blocks),
                completed_blocks=completed_blocks,
                output_file=str(out_file),
                completed=False,
            )

            generated_chunks = self._generate_missing_chunks(
                chapter_label=chapter_label,
                chapter_key=chapter_key,
                chapter_path=chapter.path,
                blocks=blocks,
                completed_blocks=completed_blocks,
                settings=settings,
                temp_dir_path=temp_dir_path,
                out_file=out_file,
                progress=progress,
                stream=stream,
            )

            block_audio_files = self._collect_block_audio_files(blocks, temp_dir_path)

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

            logger.info(
                "Chapter %s | completed | generated_chunks=%s reused_chunks=%s output=%s bytes=%s",
                chapter_label,
                generated_chunks,
                len(blocks) - generated_chunks,
                out_file,
                out_file.stat().st_size,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Chapter %s | failed | error=%s", chapter_label, exc)
        return False

    def _generate_missing_chunks(
        self,
        *,
        chapter_label: str,
        chapter_key: str,
        chapter_path: str,
        blocks: list[NarrationBlock],
        completed_blocks: int,
        settings: AudioSettings,
        temp_dir_path: Path,
        out_file: Path,
        progress: ProgressIndex,
        stream: bool,
    ) -> int:
        generated_chunks = 0
        for paragraph_index in _pending_block_indexes(completed_blocks, len(blocks)):
            block = blocks[paragraph_index - 1]
            tts_text = _strip_inline_tags_for_tts(block.text)
            if not _has_spoken_text(tts_text):
                logger.debug(
                    "Chapter %s | chunk %s/%s | skipped (empty after XML tag cleanup)",
                    chapter_label,
                    paragraph_index,
                    len(blocks),
                )
                progress.upsert_chapter_progress(
                    chapter_key=chapter_key,
                    chapter_path=chapter_path,
                    total_blocks=len(blocks),
                    completed_blocks=paragraph_index,
                    output_file=str(out_file),
                    completed=False,
                )
                continue
            logger.debug(
                "Chapter %s | chunk %s/%s | start | tag=%s chars=%s",
                chapter_label,
                paragraph_index,
                len(blocks),
                block.tag,
                len(tts_text),
            )
            response = self.audio_generator.generate(
                AudioRequest(
                    model=settings.model,
                    text=tts_text,
                    voice=settings.voice,
                    instructions=_instruction_for_block(block, settings),
                ),
                stream=stream,
            )
            chunk_path = temp_dir_path / f"chunk_{paragraph_index}.{response.format}"
            chunk_path.write_bytes(response.audio_bytes)
            generated_chunks += 1
            logger.debug(
                "Chapter %s | chunk %s/%s | done | bytes=%s path=%s",
                chapter_label,
                paragraph_index,
                len(blocks),
                len(response.audio_bytes),
                chunk_path,
            )
            progress.upsert_chapter_progress(
                chapter_key=chapter_key,
                chapter_path=chapter_path,
                total_blocks=len(blocks),
                completed_blocks=paragraph_index,
                output_file=str(out_file),
                completed=False,
            )
        return generated_chunks

    @staticmethod
    def _collect_block_audio_files(blocks: list[NarrationBlock], temp_dir_path: Path) -> list[tuple[NarrationBlock, Path]]:
        chunk_pairs = [
            (block, _chunk_path_for_index(temp_dir_path, paragraph_index))
            for paragraph_index, block in enumerate(blocks, start=1)
        ]
        missing_indexes = [idx for idx, (_, chunk_path) in enumerate(chunk_pairs, start=1) if chunk_path is None]
        if missing_indexes:
            raise RuntimeError(f"Missing chunk for paragraph {missing_indexes[0]}")
        return [(block, chunk_path) for block, chunk_path in chunk_pairs if chunk_path is not None]

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

