from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from audiobook_generator_cli.application.services.audiobook_orchestrator import AudiobookOrchestrator
from audiobook_generator_cli.domain.models import AudioSettings
from audiobook_generator_cli.domain.ports import AudioGeneratorPort
from audiobook_generator_cli.infrastructure.epub.epub_repository import ZipEpubRepository
from audiobook_generator_cli.infrastructure.llm.ollama_audio_generator import OllamaAudioGenerator
from audiobook_generator_cli.infrastructure.llm.openai_speech_audio_generator import (
    OpenAISpeechAudioGenerator,
)
from audiobook_generator_cli.infrastructure.logging.logger_factory import (
    configure_logging,
    create_logger,
)

console = Console()
logger = create_logger(__name__)

# Supported voice backend identifiers.
_BACKEND_OLLAMA = "ollama"
_BACKEND_OPENAI_SPEECH = "openai-speech"
_VOICE_BACKENDS = (_BACKEND_OLLAMA, _BACKEND_OPENAI_SPEECH)


def _abort(msg: str) -> None:
    """Print an error and exit with code 1 before any processing begins."""
    console.print(f"[bold red]Error:[/bold red] {msg}")
    raise typer.Exit(code=1)


def _build_audio_generator(backend: str, base_url: str) -> AudioGeneratorPort:
    """Instantiate the correct ``AudioGeneratorPort`` implementation."""
    if backend == _BACKEND_OPENAI_SPEECH:
        return OpenAISpeechAudioGenerator(base_url=base_url)
    # Default: ollama /api/generate
    return OllamaAudioGenerator(base_url=base_url)


def generate(
    in_path: Annotated[Path, typer.Option("--in", help="Input EPUB file path")],
    voice_model: Annotated[str, typer.Option("--voice-model", help="TTS model name")],
    out_path: Annotated[
        Optional[Path],
        typer.Option(
            "--out",
            help="Directory to write per-chapter audio files "
            "(default: <in_stem>_audiobook/)",
        ),
    ] = None,
    voice_base_url: Annotated[
        str,
        typer.Option(
            "--voice-base-url",
            help="Base URL of the TTS server (default: http://localhost:11434 "
            "for ollama, http://localhost:8000 for openai-speech/qwen-tts-api)",
        ),
    ] = "",
    voice_backend: Annotated[
        str,
        typer.Option(
            "--voice-backend",
            help=(
                f"TTS backend to use: {' | '.join(_VOICE_BACKENDS)}. "
                "'openai-speech' targets POST /v1/audio/speech "
                "(Orpheus-FastAPI, Kokoro-FastAPI, …); "
                "'ollama' targets /api/generate"
            ),
        ),
    ] = _BACKEND_OPENAI_SPEECH,
    voice: Annotated[
        str,
        typer.Option(
            "--voice",
            help=(
                "Voice name passed to the TTS backend "
                "(e.g. 'alloy' for qwen-tts-api). "
                "Default is 'alloy'."
            ),
        ),
    ] = "alloy",
    log_level: Annotated[
        str, typer.Option("--log-level", help="Logging level: DEBUG or INFO")
    ] = "INFO",
    workers: Annotated[
        int, typer.Option("--workers", min=1, max=32, help="Parallel chapter workers")
    ] = 1,
    stream: Annotated[
        bool, typer.Option("--stream/--no-stream", help="Use streaming response for TTS requests")
    ] = False,
) -> None:
    """Generate an audiobook from an EPUB using a dedicated TTS model/backend."""
    configure_logging(log_level)

    if not in_path.exists():
        _abort(f"Input file not found: {in_path}")
    if not in_path.is_file():
        _abort(f"--in must point to a file, not a directory: {in_path}")

    if voice_backend not in _VOICE_BACKENDS:
        _abort(f"--voice-backend must be one of: {', '.join(_VOICE_BACKENDS)}")

    effective_out_path = out_path or in_path.parent / (in_path.stem + "_audiobook")
    effective_out_path.mkdir(parents=True, exist_ok=True)

    # Resolve effective TTS base URL: explicit flag > per-backend default.
    if voice_base_url:
        effective_tts_url = voice_base_url
    elif voice_backend == _BACKEND_OPENAI_SPEECH:
        effective_tts_url = "http://localhost:8000"
    else:
        effective_tts_url = "http://localhost:11434"

    audio_settings = AudioSettings(
        model=voice_model,
        base_url=effective_tts_url,
        voice=voice,
    )

    logger.info(
        "Starting audiobook generation | in=%s backend=%s model=%s url=%s voice=%s out=%s",
        in_path,
        voice_backend,
        voice_model,
        effective_tts_url,
        voice or "(default)",
        effective_out_path,
    )

    epub_repo = ZipEpubRepository()
    audio_generator = _build_audio_generator(voice_backend, effective_tts_url)
    audio_orchestrator = AudiobookOrchestrator(
        epub_repository=epub_repo,
        audio_generator=audio_generator,
    )

    start = time.perf_counter()
    chapters_written = audio_orchestrator.generate(
        translated_epub_path=in_path,
        audiobook_dir=effective_out_path,
        settings=audio_settings,
        workers=workers,
        stream=stream,
    )
    end = time.perf_counter()

    elapsed = end - start
    hh, rem = divmod(int(elapsed), 3600)
    mm, ss = divmod(rem, 60)
    duration_hms = f"{hh:02d}:{mm:02d}:{ss:02d}"

    logger.info(
        "Audiobook generation finished | chapters_written=%s out=%s in %s",
        chapters_written,
        effective_out_path,
        duration_hms,
    )

    console.print(
        json.dumps(
            {
                "out_path": str(effective_out_path),
                "chapters_written": chapters_written,
                "audio_duration": duration_hms,
            },
            indent=2,
        )
    )

    raise typer.Exit(code=0)
