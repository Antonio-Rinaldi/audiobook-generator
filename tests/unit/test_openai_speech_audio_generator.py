from __future__ import annotations

import io
import wave

import pytest

from audiobook_generator_cli.domain.errors import (
    NonRetryableTranslationError,
    RetryableTranslationError,
)
from audiobook_generator_cli.domain.models import AudioRequest
from audiobook_generator_cli.infrastructure.llm.openai_speech_audio_generator import (
    OpenAISpeechAudioGenerator,
)


class _FakeResponse:
    def __init__(self, audio: bytes) -> None:
        self.status_code = 200
        self.content = audio
        self.headers = {"content-type": "audio/wav"}
        self.text = ""

    def iter_content(self, chunk_size: int = 8192) -> object:
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


def test_generate_makes_single_api_call_with_full_text(monkeypatch: object) -> None:
    calls: list[dict] = []
    wav = _wav_bytes(500)

    def fake_post(url: str, json: dict, timeout: float) -> _FakeResponse:
        _ = url
        _ = timeout
        calls.append(json)
        return _FakeResponse(wav)

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "audiobook_generator_cli.infrastructure.llm.openai_speech_audio_generator.requests.post",
        fake_post,
    )

    gen = OpenAISpeechAudioGenerator(base_url="http://localhost:8000")
    text = "Prima frase molto lunga. Seconda frase ancora lunga. Terza frase finale."
    req = AudioRequest(model="Qwen/Qwen3-TTS-12Hz-0.6B-Base", voice="alloy", text=text)

    resp = gen.generate(req)

    assert resp.format == "wav"
    assert len(calls) == 1
    assert calls[0]["input"] == text


def test_generate_stream_mode_returns_audio_response(monkeypatch: object) -> None:
    calls: list[dict] = []
    wav = _wav_bytes(300)

    def fake_post(
        url: str, json: dict, timeout: float, stream: bool = False
    ) -> _FakeResponse:
        _ = url
        _ = timeout
        _ = stream
        calls.append(json)
        return _FakeResponse(wav)

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "audiobook_generator_cli.infrastructure.llm.openai_speech_audio_generator.requests.post",
        fake_post,
    )

    gen = OpenAISpeechAudioGenerator(base_url="http://localhost:8000")
    text = "Prima frase molto lunga. Seconda frase ancora lunga. Terza frase finale."
    req = AudioRequest(model="mistralai/Voxtral-4B-TTS-2603", voice="gold", text=text)

    resp = gen.generate(req, stream=True)

    assert resp.format == "wav"
    assert len(calls) == 1
    assert calls[0]["input"] == text
    with wave.open(io.BytesIO(resp.audio_bytes), "rb") as reader:
        assert reader.getnframes() == 300


def test_generate_stream_mode_400_raises_non_retryable(monkeypatch: object) -> None:
    class _ConsumedErrorResponse:
        status_code = 400
        headers = {"content-type": "application/json"}

        @property
        def content(self) -> bytes:
            raise RuntimeError("The content for this response was already consumed")

        def iter_content(self, chunk_size: int = 8192) -> object:
            _ = chunk_size
            yield b'{"error":"bad request"}'

    def fake_post(
        url: str, json: dict, timeout: float, stream: bool = False
    ) -> _ConsumedErrorResponse:
        _ = url
        _ = json
        _ = timeout
        _ = stream
        return _ConsumedErrorResponse()

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "audiobook_generator_cli.infrastructure.llm.openai_speech_audio_generator.requests.post",
        fake_post,
    )

    gen = OpenAISpeechAudioGenerator(base_url="http://localhost:8000")
    req = AudioRequest(model="mistralai/Voxtral-4B-TTS-2603", voice="gold", text="ciao")

    with pytest.raises(NonRetryableTranslationError):
        gen.generate(req, stream=True)


def test_generate_500_raises_retryable(monkeypatch: object) -> None:
    class _ServerErrorResponse:
        status_code = 500
        headers = {"content-type": "application/json"}
        content = b'{"error":"internal server error"}'

    def fake_post(url: str, json: dict, timeout: float) -> _ServerErrorResponse:
        _ = url
        _ = json
        _ = timeout
        return _ServerErrorResponse()

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "audiobook_generator_cli.infrastructure.llm.openai_speech_audio_generator.requests.post",
        fake_post,
    )

    gen = OpenAISpeechAudioGenerator(base_url="http://localhost:8000")
    req = AudioRequest(model="Qwen/model", voice="alloy", text="ciao")

    with pytest.raises(RetryableTranslationError):
        gen.generate(req)


def test_generate_network_error_raises_retryable(monkeypatch: object) -> None:
    import requests as req_lib

    def fake_post(url: str, json: dict, timeout: float) -> None:
        _ = url
        _ = json
        _ = timeout
        raise req_lib.ConnectionError("refused")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "audiobook_generator_cli.infrastructure.llm.openai_speech_audio_generator.requests.post",
        fake_post,
    )

    gen = OpenAISpeechAudioGenerator(base_url="http://localhost:8000")
    req = AudioRequest(model="Qwen/model", voice="alloy", text="ciao")

    with pytest.raises(RetryableTranslationError):
        gen.generate(req)


def test_generate_400_non_stream_includes_error_body_excerpt(monkeypatch: object) -> None:
    class _BadRequestResponse:
        status_code = 400
        headers = {"content-type": "application/json"}
        content = b'{"error": "voice not found"}'

    def fake_post(url: str, json: dict, timeout: float) -> _BadRequestResponse:
        _ = url
        _ = json
        _ = timeout
        return _BadRequestResponse()

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "audiobook_generator_cli.infrastructure.llm.openai_speech_audio_generator.requests.post",
        fake_post,
    )

    gen = OpenAISpeechAudioGenerator(base_url="http://localhost:8000")
    req = AudioRequest(model="Qwen/model", voice="unknown", text="ciao")

    with pytest.raises(NonRetryableTranslationError, match="voice not found"):
        gen.generate(req)


def test_generate_empty_response_body_raises_retryable(monkeypatch: object) -> None:
    class _EmptyResponse:
        status_code = 200
        headers = {"content-type": "audio/wav"}
        content = b""

    def fake_post(url: str, json: dict, timeout: float) -> _EmptyResponse:
        _ = url
        _ = json
        _ = timeout
        return _EmptyResponse()

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "audiobook_generator_cli.infrastructure.llm.openai_speech_audio_generator.requests.post",
        fake_post,
    )

    gen = OpenAISpeechAudioGenerator(base_url="http://localhost:8000")
    req = AudioRequest(model="Qwen/model", voice="alloy", text="ciao")

    with pytest.raises(RetryableTranslationError, match="Empty audio response"):
        gen.generate(req)


def test_generate_includes_instructions_in_payload(monkeypatch: object) -> None:
    captured_payloads: list[dict] = []

    def fake_post(url: str, json: dict, timeout: float) -> _FakeResponse:
        _ = url
        _ = timeout
        captured_payloads.append(json)
        return _FakeResponse(_wav_bytes(80))

    monkeypatch.setattr(  # type: ignore[attr-defined]
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
    assert captured_payloads[0]["input"] == "Titolo del capitolo."