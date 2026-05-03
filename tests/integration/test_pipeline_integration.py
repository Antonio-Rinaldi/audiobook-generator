from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from pathlib import Path

from audiobook_generator_cli.application.services.audiobook_orchestrator import (
    AudiobookOrchestrator,
)
from audiobook_generator_cli.domain.models import AudioRequest, AudioResponse, AudioSettings
from audiobook_generator_cli.domain.ports import AudioGeneratorPort
from audiobook_generator_cli.infrastructure.epub.epub_repository import ZipEpubRepository

_FIXTURE_EPUB = Path(__file__).parent / "fixtures" / "minimal.epub"


def _minimal_wav() -> bytes:
    """Return a valid single-frame WAV payload for testing."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(22050)
        writer.writeframes(b"\x00\x00")
    return buf.getvalue()


@dataclass(frozen=True)
class FakeAudioGenerator(AudioGeneratorPort):
    """Returns minimal WAV bytes without calling any external service."""

    def generate(self, request: AudioRequest, stream: bool = False) -> AudioResponse:
        _ = request
        _ = stream
        return AudioResponse(audio_bytes=_minimal_wav(), format="wav")


def test_pipeline_writes_one_wav_for_single_chapter_epub(tmp_path: Path) -> None:
    epub_repository = ZipEpubRepository()
    orchestrator = AudiobookOrchestrator(
        epub_repository=epub_repository,
        audio_generator=FakeAudioGenerator(),
    )

    written = orchestrator.generate(
        translated_epub_path=_FIXTURE_EPUB,
        audiobook_dir=tmp_path / "audio",
        settings=AudioSettings(model="test-model"),
    )

    assert written == 1
    wav_files = list((tmp_path / "audio").glob("*.wav"))
    assert len(wav_files) == 1


def test_pipeline_progress_index_marks_chapter_completed(tmp_path: Path) -> None:
    import json  # noqa: PLC0415

    epub_repository = ZipEpubRepository()
    orchestrator = AudiobookOrchestrator(
        epub_repository=epub_repository,
        audio_generator=FakeAudioGenerator(),
    )

    orchestrator.generate(
        translated_epub_path=_FIXTURE_EPUB,
        audiobook_dir=tmp_path / "audio",
        settings=AudioSettings(model="test-model"),
    )

    progress_file = tmp_path / "audio" / ".audiobook_progress.json"
    assert progress_file.exists()

    index = json.loads(progress_file.read_text(encoding="utf-8"))
    chapters = index.get("chapters", {})
    assert chapters, "progress index must contain at least one chapter entry"
    chapter_state = next(iter(chapters.values()))
    assert chapter_state.get("completed") is True