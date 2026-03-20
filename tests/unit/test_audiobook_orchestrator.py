from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from audiobook_generator_cli.application.services.audiobook_orchestrator import AudiobookOrchestrator
from audiobook_generator_cli.domain.models import AudioRequest, AudioResponse, AudioSettings, ChapterDocument
from audiobook_generator_cli.domain.ports import AudioGeneratorPort, EpubBook, EpubRepositoryPort


@dataclass(frozen=True)
class FakeRepo(EpubRepositoryPort):
    book: EpubBook

    def load(self, input_path: Path) -> EpubBook:
        return self.book

    def save(self, book: EpubBook, output_path: Path) -> None:
        return None


@dataclass(frozen=True)
class FakeAudio(AudioGeneratorPort):
    def generate(self, request: AudioRequest) -> AudioResponse:
        return AudioResponse(audio_bytes=b"RIFF-fake", format="wav")


def test_generate_writes_one_file_for_non_empty_chapter(tmp_path: Path) -> None:
    chapter = ChapterDocument(
        path="OEBPS/ch1.xhtml",
        xhtml_bytes=(
            b"<?xml version='1.0' encoding='utf-8'?>"
            b"<html xmlns='http://www.w3.org/1999/xhtml'><body><p>Hello</p></body></html>"
        ),
    )
    empty_chapter = ChapterDocument(
        path="OEBPS/ch2.xhtml",
        xhtml_bytes=(
            b"<?xml version='1.0' encoding='utf-8'?>"
            b"<html xmlns='http://www.w3.org/1999/xhtml'><body><div>x</div></body></html>"
        ),
    )

    book = EpubBook(
        items={"mimetype": b"application/epub+zip", chapter.path: chapter.xhtml_bytes},
        chapters=[chapter, empty_chapter],
    )

    orchestrator = AudiobookOrchestrator(epub_repository=FakeRepo(book=book), audio_generator=FakeAudio())

    written = orchestrator.generate(
        translated_epub_path=tmp_path / "in.epub",
        audiobook_dir=tmp_path / "audio",
        settings=AudioSettings(model="voice-model"),
    )

    assert written == 1
    assert (tmp_path / "audio" / "ch1.wav").exists()
