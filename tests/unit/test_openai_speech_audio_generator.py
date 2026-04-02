from __future__ import annotations

import io
import wave

import pytest

from audiobook_generator_cli.domain.models import AudioRequest
from audiobook_generator_cli.domain.errors import NonRetryableTranslationError
from audiobook_generator_cli.infrastructure.llm.openai_speech_audio_generator import (
    OpenAISpeechAudioGenerator,
    _split_text_semantic,
)


class _FakeResponse:
    def __init__(self, audio: bytes) -> None:
        self.status_code = 200
        self.content = audio
        self.headers = {"content-type": "audio/wav"}
        self.text = ""

    def iter_content(self, chunk_size: int = 8192):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i : i + chunk_size]


def _wav_bytes(duration_frames: int, *, framerate: int = 22050) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(framerate)
        writer.writeframes(b"\x00\x00" * duration_frames)
    return out.getvalue()


def test_split_text_semantic_respects_max_chars() -> None:
    text = "\n\n".join(
        [
            "Titolo capitolo",
            "Prima frase. Seconda frase. Terza frase.",
            "Quarto blocco con più testo.",
        ]
    )
    chunks = _split_text_semantic(text, 30)

    assert chunks
    assert all(len(chunk) <= 30 for chunk in chunks)


def test_generate_splits_and_merges_wav(monkeypatch) -> None:
    calls: list[str] = []
    wav_a = _wav_bytes(200)
    wav_b = _wav_bytes(300)

    def fake_post(url: str, json: dict, timeout: float):
        _ = url
        _ = timeout
        calls.append(json["input"])
        return _FakeResponse(wav_a if len(calls) == 1 else wav_b)

    monkeypatch.setattr(
        "audiobook_generator_cli.infrastructure.llm.openai_speech_audio_generator.requests.post",
        fake_post,
    )

    gen = OpenAISpeechAudioGenerator(base_url="http://localhost:8000", max_chars_per_request=25)
    req = AudioRequest(
        model="Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        voice="alloy",
        text="Prima frase molto lunga. Seconda frase ancora lunga. Terza frase finale.",
    )

    resp = gen.generate(req)

    assert resp.format == "wav"
    assert len(calls) >= 2

    expected_frames = 200 + 300 * (len(calls) - 1)
    with wave.open(io.BytesIO(resp.audio_bytes), "rb") as reader:
        assert reader.getnframes() == expected_frames


def test_generate_stream_mode_returns_audio_response(monkeypatch) -> None:
    calls: list[str] = []
    wav_a = _wav_bytes(120)
    wav_b = _wav_bytes(180)

    def fake_post(url: str, json: dict, timeout: float, stream: bool = False):
        _ = url
        _ = timeout
        _ = stream
        calls.append(json["input"])
        return _FakeResponse(wav_a if len(calls) == 1 else wav_b)

    monkeypatch.setattr(
        "audiobook_generator_cli.infrastructure.llm.openai_speech_audio_generator.requests.post",
        fake_post,
    )

    gen = OpenAISpeechAudioGenerator(base_url="http://localhost:8000", max_chars_per_request=24)
    req = AudioRequest(
        model="mistralai/Voxtral-4B-TTS-2603",
        voice="gold",
        text="Prima frase molto lunga. Seconda frase ancora lunga. Terza frase finale.",
    )

    resp = gen.generate(req, stream=True)

    assert resp.format == "wav"
    assert len(calls) >= 2

    expected_frames = 120 + 180 * (len(calls) - 1)
    with wave.open(io.BytesIO(resp.audio_bytes), "rb") as reader:
        assert reader.getnframes() == expected_frames


def test_generate_stream_mode_400_raises_non_retryable(monkeypatch) -> None:
    class _ConsumedErrorResponse:
        status_code = 400
        headers = {"content-type": "application/json"}

        @property
        def content(self) -> bytes:
            raise RuntimeError("The content for this response was already consumed")

        def iter_content(self, chunk_size: int = 8192):
            _ = chunk_size
            yield b'{"error":"bad request"}'

    def fake_post(url: str, json: dict, timeout: float, stream: bool = False):
        _ = url
        _ = json
        _ = timeout
        _ = stream
        return _ConsumedErrorResponse()

    monkeypatch.setattr(
        "audiobook_generator_cli.infrastructure.llm.openai_speech_audio_generator.requests.post",
        fake_post,
    )

    gen = OpenAISpeechAudioGenerator(base_url="http://localhost:8000")
    req = AudioRequest(model="mistralai/Voxtral-4B-TTS-2603", voice="gold", text="ciao")

    with pytest.raises(NonRetryableTranslationError):
        gen.generate(req, stream=True)


def test_generate_includes_instructions_in_payload(monkeypatch) -> None:
    captured_payloads: list[dict] = []

    def fake_post(url: str, json: dict, timeout: float):
        _ = url
        _ = timeout
        captured_payloads.append(json)
        return _FakeResponse(_wav_bytes(80))

    monkeypatch.setattr(
        "audiobook_generator_cli.infrastructure.llm.openai_speech_audio_generator.requests.post",
        fake_post,
    )

    gen = OpenAISpeechAudioGenerator(base_url="http://localhost:8000")
    req = AudioRequest(
        model="mlx-community/Voxtral-4B-TTS-2603-mlx-4bit",
        voice="gold",
        text="Titolo del capitolo.",
        instructions="read headings with a formal tone",
    )

    _ = gen.generate(req)

    assert captured_payloads
    assert captured_payloads[0]["instructions"] == "read headings with a formal tone"


