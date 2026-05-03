from __future__ import annotations

from pathlib import Path

from pydub import AudioSegment  # type: ignore[import-untyped]

from audiobook_generator_cli.application.models import (
    NarrationBlock,
    _chapter_tmp_dir,
)


def _chunk_path_for_index(chapter_tmp_dir: Path, paragraph_index: int) -> Path | None:
    """Resolve chunk path for a paragraph index or ``None`` if missing."""
    matches = sorted(chapter_tmp_dir.glob(f"chunk_{paragraph_index}.*"))
    return matches[0] if matches else None


def merge_temp_chunks(
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


__all__ = ["merge_temp_chunks", "_chunk_path_for_index", "_chapter_tmp_dir"]