from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from audiobook_generator_cli.application.merge import (
    _chunk_path_for_index,
    merge_temp_chunks,
)
from audiobook_generator_cli.application.models import (
    ChapterJob,
    NarrationBlock,
    PreparedChapter,
)
from audiobook_generator_cli.application.progress import ProgressIndex
from audiobook_generator_cli.application.text import (
    _has_spoken_text,
    _instruction_for_block,
    _strip_inline_tags_for_tts,
    extract_narration_blocks,
)
from audiobook_generator_cli.domain.errors import AudiobookGeneratorError
from audiobook_generator_cli.domain.models import AudioRequest, AudioSettings
from audiobook_generator_cli.domain.ports import AudioGeneratorPort, EpubRepositoryPort
from audiobook_generator_cli.infrastructure.logging.logger_factory import create_logger

logger = create_logger(__name__)

_PROGRESS_INDEX_FILE = ".audiobook_progress.json"
_TEMP_CHUNKS_DIR = ".audio_chunks"
_CHAPTER_XML_DIR = ".chapters"


def _resume_completed_blocks(
    total_blocks: int, stored_progress: int, existing_chunks: int
) -> int:
    """Resolve the resume cursor by combining index state and chunk files on disk."""
    resolved = max(stored_progress, existing_chunks)
    return max(0, min(total_blocks, resolved))


def _extract_narration_blocks(xhtml_bytes: bytes) -> list[NarrationBlock]:
    """Backward-compatible shim: delegates to application.text."""
    return extract_narration_blocks(xhtml_bytes)


def _extract_paragraphs(xhtml_bytes: bytes) -> list[str]:
    """Backward-compatible helper used by tests."""
    return [block.text for block in extract_narration_blocks(xhtml_bytes)]


def _count_contiguous_existing_chunks(chapter_tmp_dir: Path, total_blocks: int) -> int:
    """Count contiguous chunk files from paragraph 1 without gaps."""
    count = 0
    for paragraph_index in range(1, total_blocks + 1):
        if _chunk_path_for_index(chapter_tmp_dir, paragraph_index) is None:
            break
        count = paragraph_index
    return count


def _pending_block_indexes(completed_blocks: int, total_blocks: int) -> range:
    """Return paragraph index range that still requires synthesis."""
    return range(completed_blocks + 1, total_blocks + 1)


def _chapter_tmp_dir(audiobook_dir: Path, chapter_index: int, chapter_path: str) -> Path:
    """Backward-compatible re-export used by tests."""
    from audiobook_generator_cli.application.models import (
        _chapter_tmp_dir as _impl,  # noqa: PLC0415
    )

    return _impl(audiobook_dir, chapter_index, chapter_path)


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
    def _is_chapter_done(chapter_state: dict[str, object], output_path: Path) -> bool:
        """Check if a chapter is already fully completed on disk and in index."""
        return bool(chapter_state.get("completed")) and output_path.exists()

    @staticmethod
    def _log_resume_cursor(
        job: ChapterJob, completed_blocks: int, total_blocks: int
    ) -> None:
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
        blocks = extract_narration_blocks(job.chapter.xhtml_bytes)
        if not blocks:
            logger.info("Chapter %s | skipped (no narratable blocks)", job.label)
            return None

        chapter_state = job.progress.get_chapter(job.chapter_key)
        if self._is_chapter_done(chapter_state, job.output_path):
            logger.info("Chapter %s | already completed, skipping", job.label)
            return None

        job.temp_dir.mkdir(parents=True, exist_ok=True)
        existing_chunks = _count_contiguous_existing_chunks(job.temp_dir, len(blocks))
        raw_progress = chapter_state.get("completed_blocks") or 0
        stored_completed_blocks = (
            int(raw_progress) if isinstance(raw_progress, (int, float)) else 0
        )
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
    def _mark_chapter_progress(
        prepared: PreparedChapter, completed_blocks: int, completed: bool
    ) -> None:
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
        block_audio_files = self._collect_block_audio_files(
            prepared.blocks, prepared.job.temp_dir
        )
        merge_temp_chunks(
            block_audio_files=block_audio_files,
            out_file=prepared.job.output_path,
            paragraph_pause_ms=prepared.job.settings.paragraph_pause_ms,
            output_format=prepared.job.output_format,
        )
        self._cleanup_temp_dir(prepared.job.temp_dir)
        self._mark_chapter_progress(
            prepared, completed_blocks=len(prepared.blocks), completed=True
        )

        logger.info(
            "Chapter %s | completed | generated_chunks=%s reused_chunks=%s "
            "output=%s bytes=%s",
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
            self._mark_chapter_progress(
                prepared, completed_blocks=prepared.completed_blocks, completed=False
            )
            generated_chunks = self._generate_missing_chunks(prepared=prepared)
            self._finalize_chapter(prepared, generated_chunks)
            return True
        except AudiobookGeneratorError:
            raise
        except Exception as exc:
            raise AudiobookGeneratorError(
                f"Unexpected error processing chapter {prepared.job.label}: {exc}"
            ) from exc

    def _generate_missing_chunks(
        self,
        *,
        prepared: PreparedChapter,
    ) -> int:
        """Generate missing paragraph chunks for a prepared chapter."""
        generated_chunks = 0
        for paragraph_index in _pending_block_indexes(
            prepared.completed_blocks, len(prepared.blocks)
        ):
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
            chunk_path = (
                prepared.job.temp_dir / f"chunk_{paragraph_index}.{response.format}"
            )
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
    def _collect_block_audio_files(
        blocks: list[NarrationBlock], temp_dir_path: Path
    ) -> list[tuple[NarrationBlock, Path]]:
        """Resolve all chunk files and ensure no paragraph chunk is missing before merge."""
        chunk_pairs = [
            (block, _chunk_path_for_index(temp_dir_path, paragraph_index))
            for paragraph_index, block in enumerate(blocks, start=1)
        ]
        missing_indexes = [
            idx
            for idx, (_, chunk_path) in enumerate(chunk_pairs, start=1)
            if chunk_path is None
        ]
        if missing_indexes:
            raise RuntimeError(f"Missing chunk for paragraph {missing_indexes[0]}")
        return [
            (block, chunk_path)
            for block, chunk_path in chunk_pairs
            if chunk_path is not None
        ]

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

        Chapters are processed in parallel, but a single chapter is processed by
        one worker.
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
                "Resume index writes are serialized; paragraph-level checkpoints "
                "are more deterministic with --workers 1"
            )
        book = self.epub_repository.load(translated_epub_path)
        total = len(book.chapters)
        progress = ProgressIndex(path=audiobook_dir / _PROGRESS_INDEX_FILE, lock=Lock())
        written = 0
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
                for i, chapter in enumerate(book.chapters)
            )
            for future in as_completed(futures):
                try:
                    if future.result():
                        written += 1
                except AudiobookGeneratorError as exc:
                    logger.error("Chapter failed | error=%s", exc)

        logger.info(
            "Audiobook generation complete | written=%s/%s dir=%s",
            written,
            total,
            audiobook_dir,
        )
        return written