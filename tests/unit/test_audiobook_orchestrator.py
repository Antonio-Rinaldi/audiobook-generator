from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

from pydub import AudioSegment

import audiobook_generator_cli.application.merge as merge_module
from audiobook_generator_cli.application.services import (
    audiobook_orchestrator as orchestrator_module,
)
from audiobook_generator_cli.application.services.audiobook_orchestrator import (
    AudiobookOrchestrator,
)
from audiobook_generator_cli.domain.models import (
    AudioRequest,
    AudioResponse,
    AudioSettings,
    ChapterDocument,
)
from audiobook_generator_cli.domain.ports import AudioGeneratorPort, EpubBook, EpubRepositoryPort


@dataclass(frozen=True)
class FakeRepo(EpubRepositoryPort):
    book: EpubBook

    def load(self, input_path: Path) -> EpubBook:
        return self.book


@dataclass(frozen=True)
class FakeAudio(AudioGeneratorPort):
    def generate(self, request: AudioRequest, stream: bool = False) -> AudioResponse:
        return AudioResponse(audio_bytes=b"fake-bytes", format="wav")


class CountingAudio(AudioGeneratorPort):
    def __init__(self, fail_after: int | None = None) -> None:
        self.calls = 0
        self.fail_after = fail_after

    def generate(self, request: AudioRequest, stream: bool = False) -> AudioResponse:
        _ = request
        _ = stream
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("boom")
        return AudioResponse(audio_bytes=b"fake-bytes", format="wav")


def test_generate_writes_one_file_for_non_empty_chapter(
    tmp_path: Path, monkeypatch: object
) -> None:
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

    def _fake_from_file(*args: object, **kwargs: object) -> AudioSegment:
        return AudioSegment.empty()

    exported_formats: list[str] = []

    def _fake_export(
        self: AudioSegment, out_f: object, format: str = "wav", **kwargs: object
    ) -> None:
        exported_formats.append(format)
        Path(str(out_f)).write_bytes(b"ID3")

    monkeypatch = monkeypatch  # type: ignore[assignment]
    monkeypatch.setattr(merge_module.AudioSegment, "from_file", _fake_from_file)
    monkeypatch.setattr(merge_module.AudioSegment, "export", _fake_export)

    book = EpubBook(
        items={"mimetype": b"application/epub+zip", chapter.path: chapter.xhtml_bytes},
        chapters=[chapter, empty_chapter],
    )

    orchestrator = AudiobookOrchestrator(
        epub_repository=FakeRepo(book=book), audio_generator=FakeAudio()
    )

    written = orchestrator.generate(
        translated_epub_path=tmp_path / "in.epub",
        audiobook_dir=tmp_path / "audio",
        settings=AudioSettings(model="voice-model"),
    )

    assert written == 1
    assert (tmp_path / "audio" / "001_ch1.wav").exists()
    assert exported_formats == ["wav"]


def test_generate_applies_tone_instructions_and_paragraph_pause(
    tmp_path: Path, monkeypatch: object
) -> None:
    chapter = ChapterDocument(
        path="OEBPS/ch1.xhtml",
        xhtml_bytes=(
            b"<?xml version='1.0' encoding='utf-8'?>"
            b"<html xmlns='http://www.w3.org/1999/xhtml'><body>"
            b"<h1>Titolo</h1><p>Paragrafo uno</p><p>Paragrafo due</p>"
            b"</body></html>"
        ),
    )

    calls: list[AudioRequest] = []

    class _CollectingAudio(AudioGeneratorPort):
        def generate(
            self, request: AudioRequest, stream: bool = False
        ) -> AudioResponse:
            _ = stream
            calls.append(request)
            return AudioResponse(audio_bytes=b"fake-bytes", format="wav")

    pause_calls: list[int] = []

    def _fake_from_file(*args: object, **kwargs: object) -> AudioSegment:
        return AudioSegment.empty()

    def _fake_silent(duration: int = 0) -> AudioSegment:
        pause_calls.append(duration)
        return AudioSegment.empty()

    def _fake_export(
        self: AudioSegment, out_f: object, format: str = "mp3", **kwargs: object
    ) -> None:
        Path(str(out_f)).write_bytes(b"ID3")

    monkeypatch.setattr(merge_module.AudioSegment, "from_file", _fake_from_file)  # type: ignore[attr-defined]
    monkeypatch.setattr(merge_module.AudioSegment, "silent", _fake_silent)  # type: ignore[attr-defined]
    monkeypatch.setattr(merge_module.AudioSegment, "export", _fake_export)  # type: ignore[attr-defined]

    book = EpubBook(
        items={"mimetype": b"application/epub+zip", chapter.path: chapter.xhtml_bytes},
        chapters=[chapter],
    )

    orchestrator = AudiobookOrchestrator(
        epub_repository=FakeRepo(book=book), audio_generator=_CollectingAudio()
    )

    written = orchestrator.generate(
        translated_epub_path=tmp_path / "in.epub",
        audiobook_dir=tmp_path / "audio",
        settings=AudioSettings(
            model="voice-model",
            heading_tone="calm and authoritative",
            paragraph_tone="neutral narrative",
            paragraph_pause_ms=850,
        ),
    )

    assert written == 1
    assert len(calls) == 3
    assert "consistent tone" in calls[0].instructions
    assert "without shouting" in calls[0].instructions
    assert "calm and authoritative" in calls[0].instructions
    assert "consistent tone" in calls[1].instructions
    assert "neutral narrative" in calls[1].instructions
    assert "without shouting" not in calls[1].instructions
    assert pause_calls == [850]


def test_generate_supports_mp3_output_format_override(
    tmp_path: Path, monkeypatch: object
) -> None:
    chapter = ChapterDocument(
        path="OEBPS/ch1.xhtml",
        xhtml_bytes=(
            b"<?xml version='1.0' encoding='utf-8'?>"
            b"<html xmlns='http://www.w3.org/1999/xhtml'><body><p>Hello</p></body></html>"
        ),
    )

    exported_formats: list[str] = []

    def _fake_from_file(*args: object, **kwargs: object) -> AudioSegment:
        return AudioSegment.empty()

    def _fake_export(
        self: AudioSegment, out_f: object, format: str = "wav", **kwargs: object
    ) -> None:
        exported_formats.append(format)
        Path(str(out_f)).write_bytes(b"ID3")

    monkeypatch.setattr(merge_module.AudioSegment, "from_file", _fake_from_file)  # type: ignore[attr-defined]
    monkeypatch.setattr(merge_module.AudioSegment, "export", _fake_export)  # type: ignore[attr-defined]

    book = EpubBook(
        items={"mimetype": b"application/epub+zip", chapter.path: chapter.xhtml_bytes},
        chapters=[chapter],
    )

    orchestrator = AudiobookOrchestrator(
        epub_repository=FakeRepo(book=book), audio_generator=FakeAudio()
    )

    written = orchestrator.generate(
        translated_epub_path=tmp_path / "in.epub",
        audiobook_dir=tmp_path / "audio",
        settings=AudioSettings(model="voice-model", chapter_format="mp3"),
    )

    assert written == 1
    assert (tmp_path / "audio" / "001_ch1.mp3").exists()
    assert exported_formats == ["mp3"]


def test_generate_resumes_from_paragraph_progress_index(
    tmp_path: Path, monkeypatch: object
) -> None:
    chapter = ChapterDocument(
        path="OEBPS/ch_resume.xhtml",
        xhtml_bytes=(
            b"<?xml version='1.0' encoding='utf-8'?>"
            b"<html xmlns='http://www.w3.org/1999/xhtml'><body>"
            b"<p>Uno</p><p>Due</p><p>Tre</p>"
            b"</body></html>"
        ),
    )

    def _fake_from_file(*args: object, **kwargs: object) -> AudioSegment:
        return AudioSegment.empty()

    def _fake_export(
        self: AudioSegment, out_f: object, format: str = "wav", **kwargs: object
    ) -> None:
        Path(str(out_f)).write_bytes(b"RIFF")

    monkeypatch.setattr(merge_module.AudioSegment, "from_file", _fake_from_file)  # type: ignore[attr-defined]
    monkeypatch.setattr(merge_module.AudioSegment, "export", _fake_export)  # type: ignore[attr-defined]

    book = EpubBook(
        items={"mimetype": b"application/epub+zip", chapter.path: chapter.xhtml_bytes},
        chapters=[chapter],
    )

    from audiobook_generator_cli.domain.errors import AudiobookGeneratorError  # noqa: PLC0415

    first_audio = CountingAudio(fail_after=2)
    orchestrator = AudiobookOrchestrator(
        epub_repository=FakeRepo(book=book), audio_generator=first_audio
    )
    with contextlib.suppress(AudiobookGeneratorError):
        orchestrator.generate(
            translated_epub_path=tmp_path / "in.epub",
            audiobook_dir=tmp_path / "audio",
            settings=AudioSettings(model="voice-model"),
            workers=1,
        )
    assert first_audio.calls == 3

    second_audio = CountingAudio()
    orchestrator_resume = AudiobookOrchestrator(
        epub_repository=FakeRepo(book=book), audio_generator=second_audio
    )
    written_second = orchestrator_resume.generate(
        translated_epub_path=tmp_path / "in.epub",
        audiobook_dir=tmp_path / "audio",
        settings=AudioSettings(model="voice-model"),
        workers=1,
    )

    assert written_second == 1
    assert second_audio.calls == 1
    assert (tmp_path / "audio" / "001_ch_resume.wav").exists()
    assert (tmp_path / "audio" / ".audiobook_progress.json").exists()


def test_generate_reset_progress_restarts_from_first_paragraph(
    tmp_path: Path, monkeypatch: object
) -> None:
    chapter = ChapterDocument(
        path="OEBPS/ch_reset.xhtml",
        xhtml_bytes=(
            b"<?xml version='1.0' encoding='utf-8'?>"
            b"<html xmlns='http://www.w3.org/1999/xhtml'><body>"
            b"<p>Uno</p><p>Due</p><p>Tre</p>"
            b"</body></html>"
        ),
    )

    def _fake_from_file(*args: object, **kwargs: object) -> AudioSegment:
        return AudioSegment.empty()

    def _fake_export(
        self: AudioSegment, out_f: object, format: str = "wav", **kwargs: object
    ) -> None:
        Path(str(out_f)).write_bytes(b"RIFF")

    monkeypatch.setattr(merge_module.AudioSegment, "from_file", _fake_from_file)  # type: ignore[attr-defined]
    monkeypatch.setattr(merge_module.AudioSegment, "export", _fake_export)  # type: ignore[attr-defined]

    book = EpubBook(
        items={"mimetype": b"application/epub+zip", chapter.path: chapter.xhtml_bytes},
        chapters=[chapter],
    )

    audio_dir = tmp_path / "audio"
    chapter_tmp_dir = orchestrator_module._chapter_tmp_dir(audio_dir, 1, chapter.path)
    chapter_tmp_dir.mkdir(parents=True, exist_ok=True)
    (chapter_tmp_dir / "chunk_1.wav").write_bytes(b"old")

    progress_file = audio_dir / ".audiobook_progress.json"
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    progress_file.write_text(
        '{"version":1,"chapters":{"OEBPS/ch_reset.xhtml":'
        '{"completed_blocks":1,"completed":false}}}',
        encoding="utf-8",
    )

    counting_audio = CountingAudio()
    orchestrator = AudiobookOrchestrator(
        epub_repository=FakeRepo(book=book), audio_generator=counting_audio
    )
    written = orchestrator.generate(
        translated_epub_path=tmp_path / "in.epub",
        audiobook_dir=audio_dir,
        settings=AudioSettings(model="voice-model"),
        workers=1,
        reset_progress=True,
    )

    assert written == 1
    assert counting_audio.calls == 3
    assert (audio_dir / "001_ch_reset.wav").exists()


def test_generate_strips_inline_xml_like_tags_before_tts(
    tmp_path: Path, monkeypatch: object
) -> None:
    chapter = ChapterDocument(
        path="OEBPS/ch_tags.xhtml",
        xhtml_bytes=(
            b"<?xml version='1.0' encoding='utf-8'?>"
            b"<html xmlns='http://www.w3.org/1999/xhtml'><body>"
            b"<p>&lt;em&gt;Voce narrante&lt;/em&gt; continua.</p>"
            b"</body></html>"
        ),
    )

    captured: list[AudioRequest] = []

    class _CollectingAudio(AudioGeneratorPort):
        def generate(
            self, request: AudioRequest, stream: bool = False
        ) -> AudioResponse:
            _ = stream
            captured.append(request)
            return AudioResponse(audio_bytes=b"fake-bytes", format="wav")

    def _fake_from_file(*args: object, **kwargs: object) -> AudioSegment:
        return AudioSegment.empty()

    def _fake_export(
        self: AudioSegment, out_f: object, format: str = "wav", **kwargs: object
    ) -> None:
        Path(str(out_f)).write_bytes(b"RIFF")

    monkeypatch.setattr(merge_module.AudioSegment, "from_file", _fake_from_file)  # type: ignore[attr-defined]
    monkeypatch.setattr(merge_module.AudioSegment, "export", _fake_export)  # type: ignore[attr-defined]

    book = EpubBook(
        items={"mimetype": b"application/epub+zip", chapter.path: chapter.xhtml_bytes},
        chapters=[chapter],
    )

    orchestrator = AudiobookOrchestrator(
        epub_repository=FakeRepo(book=book), audio_generator=_CollectingAudio()
    )

    written = orchestrator.generate(
        translated_epub_path=tmp_path / "in.epub",
        audiobook_dir=tmp_path / "audio",
        settings=AudioSettings(model="voice-model"),
    )

    assert written == 1
    assert len(captured) == 1
    assert captured[0].text == "Voce narrante continua."


def test_generate_output_files_reflect_chapter_order(
    tmp_path: Path, monkeypatch: object
) -> None:
    """Chapter files should be named 001_… 002_… regardless of completion order."""

    def _make_chapter(stem: str) -> ChapterDocument:
        return ChapterDocument(
            path=f"OEBPS/{stem}.xhtml",
            xhtml_bytes=(
                b"<?xml version='1.0' encoding='utf-8'?>"
                b"<html xmlns='http://www.w3.org/1999/xhtml'><body>"
                b"<p>Text</p></body></html>"
            ),
        )

    ch1 = _make_chapter("chapter_one")
    ch2 = _make_chapter("chapter_two")
    ch3 = _make_chapter("chapter_three")

    def _fake_export(
        self: AudioSegment, out_f: object, format: str = "wav", **kwargs: object
    ) -> None:
        Path(str(out_f)).write_bytes(b"RIFF")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        merge_module.AudioSegment, "from_file", lambda *a, **kw: AudioSegment.empty()
    )
    monkeypatch.setattr(merge_module.AudioSegment, "export", _fake_export)  # type: ignore[attr-defined]

    book = EpubBook(items={}, chapters=[ch1, ch2, ch3])
    orchestrator = AudiobookOrchestrator(
        epub_repository=FakeRepo(book=book), audio_generator=FakeAudio()
    )

    orchestrator.generate(
        translated_epub_path=tmp_path / "in.epub",
        audiobook_dir=tmp_path / "audio",
        settings=AudioSettings(model="model"),
    )

    audio_dir = tmp_path / "audio"
    assert (audio_dir / "001_chapter_one.wav").exists()
    assert (audio_dir / "002_chapter_two.wav").exists()
    assert (audio_dir / "003_chapter_three.wav").exists()

    sorted_names = sorted(f.name for f in audio_dir.glob("*.wav"))
    assert sorted_names == [
        "001_chapter_one.wav",
        "002_chapter_two.wav",
        "003_chapter_three.wav",
    ]