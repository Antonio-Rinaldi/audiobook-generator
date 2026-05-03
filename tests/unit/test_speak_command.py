from __future__ import annotations

import io
import wave
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

import audiobook_generator_cli.cli as cli_module
from audiobook_generator_cli.cli import app
from audiobook_generator_cli.domain.errors import RetryableTranslationError
from audiobook_generator_cli.domain.models import AudioResponse


def _wav_bytes(frames: int = 100, *, framerate: int = 22050) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(framerate)
        w.writeframes(b"\x00\x00" * frames)
    return out.getvalue()


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _inject_mock_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    mock = MagicMock()
    mock.generate.return_value = AudioResponse(audio_bytes=_wav_bytes(), format="wav")
    monkeypatch.setattr(cli_module, "_audio_generator", mock)


def test_speak_writes_audio_file(runner: CliRunner, tmp_path: Path) -> None:
    out = tmp_path / "out.wav"
    result = runner.invoke(
        app,
        [
            "speak",
            "--text", "Hello world.",
            "--out", str(out),
            "--voice-model", "Qwen/model",
        ],
    )

    assert result.exit_code == 0, result.output
    assert out.exists()
    assert out.stat().st_size > 0


def test_speak_summary_json_in_output(runner: CliRunner, tmp_path: Path) -> None:
    out = tmp_path / "out.wav"
    result = runner.invoke(
        app,
        ["speak", "--text", "Hi.", "--out", str(out), "--voice-model", "Qwen/model"],
    )

    assert result.exit_code == 0, result.output
    assert '"out_path"' in result.output
    assert '"bytes_written"' in result.output


def test_speak_invalid_backend_exits_1(runner: CliRunner, tmp_path: Path) -> None:
    out = tmp_path / "out.wav"
    result = runner.invoke(
        app,
        [
            "speak",
            "--text", "Hello.",
            "--out", str(out),
            "--voice-model", "Qwen/model",
            "--voice-backend", "unknown-backend",
        ],
    )

    assert result.exit_code == 1


def test_speak_invalid_output_format_exits_1(runner: CliRunner, tmp_path: Path) -> None:
    out = tmp_path / "out.wav"
    result = runner.invoke(
        app,
        [
            "speak",
            "--text", "Hello.",
            "--out", str(out),
            "--voice-model", "Qwen/model",
            "--output-format", "ogg",
        ],
    )

    assert result.exit_code == 1


def test_speak_empty_text_exits_1(runner: CliRunner, tmp_path: Path) -> None:
    out = tmp_path / "out.wav"
    result = runner.invoke(
        app,
        ["speak", "--text", "   ", "--out", str(out), "--voice-model", "Qwen/model"],
    )

    assert result.exit_code == 1


def test_speak_tts_error_propagates(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = MagicMock()
    mock.generate.side_effect = RetryableTranslationError("TTS down")
    monkeypatch.setattr(cli_module, "_audio_generator", mock)

    out = tmp_path / "out.wav"
    result = runner.invoke(
        app,
        ["speak", "--text", "Hello.", "--out", str(out), "--voice-model", "Qwen/model"],
    )

    assert result.exit_code != 0


def test_speak_uninitialised_generator_exits_1(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_module, "_audio_generator", None)
    out = tmp_path / "out.wav"
    result = runner.invoke(
        app,
        ["speak", "--text", "Hello.", "--out", str(out), "--voice-model", "Qwen/model"],
    )

    assert result.exit_code == 1


def test_generate_command_still_reachable(runner: CliRunner) -> None:
    result = runner.invoke(app, ["generate", "--help"])

    assert result.exit_code == 0
    assert "--in" in result.output