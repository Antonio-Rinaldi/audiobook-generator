from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydub import AudioSegment

from audiobook_generator_cli.application.services import audiobook_orchestrator as orchestrator_module
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
    def generate(self, request: AudioRequest, stream: bool = False) -> AudioResponse:
        return AudioResponse(audio_bytes=b"fake-bytes", format="wav")


def test_generate_writes_one_file_for_non_empty_chapter(tmp_path: Path, monkeypatch) -> None:
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

    def _fake_from_file(*args, **kwargs):
        return AudioSegment.silent(duration=10)

    def _fake_export(self, out_f, format="mp3", **kwargs):
        Path(out_f).write_bytes(b"ID3")
        return None

    monkeypatch.setattr(orchestrator_module.AudioSegment, "from_file", _fake_from_file)
    monkeypatch.setattr(orchestrator_module.AudioSegment, "export", _fake_export)

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
    assert (tmp_path / "audio" / "ch1.mp3").exists()
