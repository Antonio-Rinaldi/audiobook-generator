from __future__ import annotations

import html
import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from pydub import AudioSegment

from lxml import etree

from audiobook_generator_cli.domain.models import AudioRequest, AudioSettings, ChapterDocument
from audiobook_generator_cli.domain.ports import AudioGeneratorPort, EpubRepositoryPort
from audiobook_generator_cli.infrastructure.logging.logger_factory import create_logger

logger = create_logger(__name__)

# Tags whose text content we want to extract for narration.
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_NARRATABLE_TAGS = _HEADING_TAGS | {"p", "li", "blockquote", "dd", "dt", "figcaption", "td", "th"}


_WS_RE = re.compile(r"\s+")
_INLINE_TAG_RE = re.compile(r"</?[^>]+>")
_PROGRESS_INDEX_FILE = ".audiobook_progress.json"
_TEMP_CHUNKS_DIR = ".audio_chunks"
_CHAPTER_XML_DIR = ".chapters"

_BASE_READING_INSTRUCTION = (
    "Maintain a consistent tone and stable volume across the entire passage. "
    "Strictly follow punctuation for natural narration: apply clear pauses at commas, full stops, semicolons, colons, question marks, and exclamation marks."
)
_HEADING_READING_INSTRUCTION = (
    "Read headings calmly and clearly, without shouting or abrupt emphasis."
)


def _local_tag_name(tag: str | None) -> str:
    """Return lower-cased local tag name without XML namespace prefix."""
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
    """Return True when text contains at least one alphanumeric character."""
    # Keep any block with letters/digits from any alphabet, skip punctuation-only placeholders.
    return any(ch.isalnum() for ch in text)


def _strip_inline_tags_for_tts(text: str) -> str:
    """Remove inline XML/HTML markers from text before TTS synthesis."""
    unescaped = html.unescape(text)
    without_tags = _INLINE_TAG_RE.sub(" ", unescaped)
    collapsed = _WS_RE.sub(" ", without_tags).strip()
    return re.sub(r"\s+([,.;:!?…])", r"\1", collapsed)


def _is_heading_tag(tag: str) -> bool:
    """Check whether tag belongs to heading tag set."""
    return _local_tag_name(tag) in _HEADING_TAGS


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
        """Final chapter output path for merged audio."""
        return self.audiobook_dir / f"{Path(self.chapter.path).stem}.{self.output_format}"

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


def _resume_completed_blocks(total_blocks: int, stored_progress: int, existing_chunks: int) -> int:
    """Resolve the resume cursor by combining index state and chunk files on disk."""
    resolved = max(stored_progress, existing_chunks)
    return max(0, min(total_blocks, resolved))


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
    """Return deterministic temporary directory path for one chapter."""
    stem = Path(chapter_path).stem
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]", "_", stem)
    return audiobook_dir / _TEMP_CHUNKS_DIR / f"chapter_{chapter_index}_{safe_stem}"


def _chunk_path_for_index(chapter_tmp_dir: Path, paragraph_index: int) -> Path | None:
    """Resolve chunk path for a paragraph index or ``None`` if missing."""
    matches = sorted(chapter_tmp_dir.glob(f"chunk_{paragraph_index}.*"))
    return matches[0] if matches else None


def _count_contiguous_existing_chunks(chapter_tmp_dir: Path, total_blocks: int) -> int:
    """Count contiguous chunk files from paragraph 1 without gaps."""
    count = 0
    for paragraph_index in range(1, total_blocks + 1):
        if _chunk_path_for_index(chapter_tmp_dir, paragraph_index) is None:
            break
        count = paragraph_index
    return count


@dataclass
class ProgressIndex:
    """Thread-safe checkpoint persistence for chapter and paragraph progress."""

    path: Path
    lock: Lock

    def _load_unlocked(self) -> dict:
        """Load persisted progress payload without acquiring outer lock."""
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
        """Persist progress payload atomically without acquiring outer lock."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(self.path)

    def get_chapter(self, chapter_key: str) -> dict:
        """Return shallow copy of one chapter state from progress index."""
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
        """Insert or update chapter progress state in checkpoint file."""
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
    """Build narration instructions according to block type and user tone flags."""
    parts = [_BASE_READING_INSTRUCTION]
    if block.is_heading:
        parts.append(_HEADING_READING_INSTRUCTION)
        if settings.heading_tone:
            parts.append(settings.heading_tone)
    elif settings.paragraph_tone:
        parts.append(settings.paragraph_tone)
    return "\n".join(parts)


def _merge_temp_chunks(
    block_audio_files: list[tuple[NarrationBlock, Path]],
    out_file: Path,
    paragraph_pause_ms: int,
    output_format: str,
) -> None:
    """Merge chunk audio files into final chapter file with paragraph pauses."""
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
    """Return paragraph index range that still requires synthesis."""
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
        """Delete resume metadata and temporary chunks for a full fresh run."""
        index_path = audiobook_dir / _PROGRESS_INDEX_FILE
        temp_chunks_dir = audiobook_dir / _TEMP_CHUNKS_DIR

        if index_path.exists():
            index_path.unlink(missing_ok=True)
            logger.info("Removed progress index | path=%s", index_path)

        if temp_chunks_dir.exists():
            shutil.rmtree(temp_chunks_dir, ignore_errors=True)
            logger.info("Removed temp chunk directory | path=%s", temp_chunks_dir)

    @staticmethod
    def _chapter_xml_dir(audiobook_dir: Path) -> Path:
        """Return directory where chapter XML snapshots are stored."""
        return audiobook_dir / _CHAPTER_XML_DIR

    def _persist_chapter_xml(self, job: ChapterJob) -> None:
        """Write raw chapter XHTML to disk to help debugging and auditability."""
        chapter_xml_dir = self._chapter_xml_dir(job.audiobook_dir)
        chapter_xml_dir.mkdir(parents=True, exist_ok=True)
        (chapter_xml_dir / f"chapter_{job.index}.xml").write_bytes(job.chapter.xhtml_bytes)

    @staticmethod
    def _is_chapter_done(chapter_state: dict, output_path: Path) -> bool:
        """Check if a chapter is already fully completed on disk and in index."""
        return bool(chapter_state.get("completed")) and output_path.exists()

    @staticmethod
    def _log_resume_cursor(job: ChapterJob, completed_blocks: int, total_blocks: int) -> None:
        """Log resume information when chapter processing starts from a non-zero chunk."""
        if completed_blocks > 0:
            logger.info(
                "Chapter %s | resume from chunk %s/%s",
                job.label,
                completed_blocks + 1,
                total_blocks,
            )

    def _prepare_chapter(self, job: ChapterJob) -> PreparedChapter | None:
        """Build immutable synthesis inputs for one chapter or return None if skipped."""
        self._persist_chapter_xml(job)
        blocks = _extract_narration_blocks(job.chapter.xhtml_bytes)
        if not blocks:
            logger.info("Chapter %s | skipped (no narratable blocks)", job.label)
            return None

        chapter_state = job.progress.get_chapter(job.chapter_key)
        if self._is_chapter_done(chapter_state, job.output_path):
            logger.info("Chapter %s | already completed, skipping", job.label)
            return None

        job.temp_dir.mkdir(parents=True, exist_ok=True)
        existing_chunks = _count_contiguous_existing_chunks(job.temp_dir, len(blocks))
        stored_completed_blocks = int(chapter_state.get("completed_blocks", 0) or 0)
        completed_blocks = _resume_completed_blocks(
            total_blocks=len(blocks),
            stored_progress=stored_completed_blocks,
            existing_chunks=existing_chunks,
        )
        self._log_resume_cursor(job, completed_blocks, len(blocks))
        logger.debug(
            "Chapter %s | temp_dir=%s existing_chunks=%s",
            job.label,
            job.temp_dir,
            existing_chunks,
        )

        return PreparedChapter(
            job=job,
            blocks=blocks,
            completed_blocks=completed_blocks,
            existing_chunks=existing_chunks,
        )

    @staticmethod
    def _mark_chapter_progress(prepared: PreparedChapter, completed_blocks: int, completed: bool) -> None:
        """Persist progress state for one chapter in resume index."""
        prepared.job.progress.upsert_chapter_progress(
            chapter_key=prepared.job.chapter_key,
            chapter_path=prepared.job.chapter.path,
            total_blocks=len(prepared.blocks),
            completed_blocks=completed_blocks,
            output_file=str(prepared.job.output_path),
            completed=completed,
        )

    @staticmethod
    def _cleanup_temp_dir(temp_dir: Path) -> None:
        """Delete temporary chunk directory after chapter merge succeeds."""
        shutil.rmtree(temp_dir, ignore_errors=True)

    def _finalize_chapter(self, prepared: PreparedChapter, generated_chunks: int) -> None:
        """Merge chunks, cleanup temp files, and mark chapter as complete."""
        block_audio_files = self._collect_block_audio_files(prepared.blocks, prepared.job.temp_dir)
        _merge_temp_chunks(
            block_audio_files=block_audio_files,
            out_file=prepared.job.output_path,
            paragraph_pause_ms=prepared.job.settings.paragraph_pause_ms,
            output_format=prepared.job.output_format,
        )
        self._cleanup_temp_dir(prepared.job.temp_dir)
        self._mark_chapter_progress(prepared, completed_blocks=len(prepared.blocks), completed=True)

        logger.info(
            "Chapter %s | completed | generated_chunks=%s reused_chunks=%s output=%s bytes=%s",
            prepared.job.label,
            generated_chunks,
            len(prepared.blocks) - generated_chunks,
            prepared.job.output_path,
            prepared.job.output_path.stat().st_size,
        )

    def _process_chapter(self, job: ChapterJob) -> bool:
        """Process one chapter end-to-end: extract, synthesize, merge, and checkpoint."""
        job.audiobook_dir.mkdir(parents=True, exist_ok=True)

        prepared = self._prepare_chapter(job)
        if prepared is None:
            return False

        logger.info(
            "Chapter %s | start | blocks=%s output=%s",
            prepared.job.label,
            len(prepared.blocks),
            prepared.job.output_path,
        )

        try:
            self._mark_chapter_progress(prepared, completed_blocks=prepared.completed_blocks, completed=False)

            generated_chunks = self._generate_missing_chunks(
                prepared=prepared,
            )
            self._finalize_chapter(prepared, generated_chunks)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Chapter %s | failed | error=%s", prepared.job.label, exc)
        return False

    def _generate_missing_chunks(
        self,
        *,
        prepared: PreparedChapter,
    ) -> int:
        """Generate missing paragraph chunks for a prepared chapter."""
        generated_chunks = 0
        for paragraph_index in _pending_block_indexes(prepared.completed_blocks, len(prepared.blocks)):
            block = prepared.blocks[paragraph_index - 1]
            tts_text = _strip_inline_tags_for_tts(block.text)
            if not _has_spoken_text(tts_text):
                logger.debug(
                    "Chapter %s | chunk %s/%s | skipped (empty after XML tag cleanup)",
                    prepared.job.label,
                    paragraph_index,
                    len(prepared.blocks),
                )
                self._mark_chapter_progress(
                    prepared=prepared,
                    completed_blocks=paragraph_index,
                    completed=False,
                )
                continue
            logger.debug(
                "Chapter %s | chunk %s/%s | start | tag=%s chars=%s",
                prepared.job.label,
                paragraph_index,
                len(prepared.blocks),
                block.tag,
                len(tts_text),
            )
            response = self.audio_generator.generate(
                AudioRequest(
                    model=prepared.job.settings.model,
                    text=tts_text,
                    voice=prepared.job.settings.voice,
                    instructions=_instruction_for_block(block, prepared.job.settings),
                ),
                stream=prepared.job.stream,
            )
            chunk_path = prepared.job.temp_dir / f"chunk_{paragraph_index}.{response.format}"
            chunk_path.write_bytes(response.audio_bytes)
            generated_chunks += 1
            logger.debug(
                "Chapter %s | chunk %s/%s | done | bytes=%s path=%s",
                prepared.job.label,
                paragraph_index,
                len(prepared.blocks),
                len(response.audio_bytes),
                chunk_path,
            )
            self._mark_chapter_progress(
                prepared=prepared,
                completed_blocks=paragraph_index,
                completed=False,
            )
        return generated_chunks

    @staticmethod
    def _collect_block_audio_files(blocks: list[NarrationBlock], temp_dir_path: Path) -> list[tuple[NarrationBlock, Path]]:
        """Resolve all chunk files and ensure no paragraph chunk is missing before merge."""
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
                    ChapterJob(
                        index=i + 1,
                        total=total,
                        chapter=chapter,
                        audiobook_dir=audiobook_dir,
                        settings=settings,
                        progress=progress,
                        stream=stream,
                    ),
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

