from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from audiobook_generator_cli.application.services.audiobook_orchestrator import AudiobookOrchestrator
from audiobook_generator_cli.domain.models import AudioSettings
from audiobook_generator_cli.domain.ports import AudioGeneratorPort
from audiobook_generator_cli.infrastructure.epub.epub_repository import ZipEpubRepository
from audiobook_generator_cli.infrastructure.llm.openai_speech_audio_generator import (
    OpenAISpeechAudioGenerator,
)
from audiobook_generator_cli.infrastructure.logging.logger_factory import (
    configure_logging,
    create_logger,
)

console = Console()
logger = create_logger(__name__)

_BACKEND_OPENAI_SPEECH = "openai-speech"
_VOICE_BACKENDS = (_BACKEND_OPENAI_SPEECH,)
_OUTPUT_FORMATS = ("wav", "mp3")


@dataclass(frozen=True)
class GenerateCommand:
    """Validated CLI command payload used by generation orchestration."""

    input_path: Path
    output_path: Path
    voice_model: str
    voice_backend: str
    voice_base_url: str
    voice: str
    log_level: str
    workers: int
    stream: bool
    heading_tone: str
    paragraph_tone: str
    paragraph_pause_ms: int
    spool_temp_chunks: bool
    output_format: str
    reset_progress: bool


def _abort(msg: str) -> None:
    """Print an error and exit with code 1 before any processing begins."""
    console.print(f"[bold red]Error:[/bold red] {msg}")
    raise typer.Exit(code=1)


def _build_audio_generator(base_url: str) -> AudioGeneratorPort:
    """Instantiate the OpenAI-compatible ``AudioGeneratorPort`` implementation."""
    return OpenAISpeechAudioGenerator(base_url=base_url)


def _validate_input_path(input_path: Path) -> None:
    """Validate that input path exists and points to a file."""
    if not input_path.exists():
        _abort(f"Input file not found: {input_path}")
    if not input_path.is_file():
        _abort(f"--in must point to a file, not a directory: {input_path}")


def _validate_backend(voice_backend: str) -> None:
    """Validate selected TTS backend against supported backends."""
    if voice_backend not in _VOICE_BACKENDS:
        _abort(f"--voice-backend must be one of: {', '.join(_VOICE_BACKENDS)}")


def _normalize_output_format(output_format: str) -> str:
    """Normalize output format and validate supported values."""
    normalized = output_format.strip().lower()
    if normalized not in _OUTPUT_FORMATS:
        _abort(f"--output-format must be one of: {', '.join(_OUTPUT_FORMATS)}")
    return normalized


def _resolve_output_path(input_path: Path, output_path: Path | None) -> Path:
    """Resolve and create effective output directory for generated chapters."""
    effective_path = output_path or input_path.parent / (input_path.stem + "_audiobook")
    effective_path.mkdir(parents=True, exist_ok=True)
    return effective_path


def _resolve_tts_url(voice_base_url: str) -> str:
    """Resolve effective TTS base URL from CLI input with sensible default."""
    return voice_base_url or "http://localhost:8000"


def _build_command(
    *,
    input_path: Path,
    output_path: Path | None,
    voice_model: str,
    voice_backend: str,
    voice_base_url: str,
    voice: str,
    log_level: str,
    workers: int,
    stream: bool,
    heading_tone: str,
    paragraph_tone: str,
    paragraph_pause_ms: int,
    spool_temp_chunks: bool,
    output_format: str,
    reset_progress: bool,
) -> GenerateCommand:
    """Build immutable command object after input validation and normalization."""
    _validate_input_path(input_path)
    _validate_backend(voice_backend)
    normalized_output_format = _normalize_output_format(output_format)
    return GenerateCommand(
        input_path=input_path,
        output_path=_resolve_output_path(input_path, output_path),
        voice_model=voice_model,
        voice_backend=voice_backend,
        voice_base_url=_resolve_tts_url(voice_base_url),
        voice=voice,
        log_level=log_level,
        workers=workers,
        stream=stream,
        heading_tone=heading_tone.strip(),
        paragraph_tone=paragraph_tone.strip(),
        paragraph_pause_ms=paragraph_pause_ms,
        spool_temp_chunks=spool_temp_chunks,
        output_format=normalized_output_format,
        reset_progress=reset_progress,
    )


def _build_audio_settings(command: GenerateCommand) -> AudioSettings:
    """Map validated CLI command values into domain ``AudioSettings``."""
    return AudioSettings(
        model=command.voice_model,
        base_url=command.voice_base_url,
        voice=command.voice,
        heading_tone=command.heading_tone,
        paragraph_tone=command.paragraph_tone,
        paragraph_pause_ms=command.paragraph_pause_ms,
        spool_temp_chunks=command.spool_temp_chunks,
        chapter_format=command.output_format,
    )


def _duration_hms(total_seconds: float) -> str:
    """Convert elapsed seconds into ``HH:MM:SS`` format."""
    hours, rem = divmod(int(total_seconds), 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _render_summary(output_path: Path, chapters_written: int, elapsed_seconds: float) -> None:
    """Render JSON summary to terminal for machine and human consumption."""
    console.print(
        json.dumps(
            {
                "out_path": str(output_path),
                "chapters_written": chapters_written,
                "audio_duration": _duration_hms(elapsed_seconds),
            },
            indent=2,
        )
    )


def _run_generation(command: GenerateCommand, settings: AudioSettings) -> tuple[int, float]:
    """Run orchestrator generation and return written chapter count plus elapsed time."""
    orchestrator = AudiobookOrchestrator(
        epub_repository=ZipEpubRepository(),
        audio_generator=_build_audio_generator(command.voice_base_url),
    )
    start = time.perf_counter()
    chapters_written = orchestrator.generate(
        translated_epub_path=command.input_path,
        audiobook_dir=command.output_path,
        settings=settings,
        workers=command.workers,
        stream=command.stream,
        reset_progress=command.reset_progress,
    )
    elapsed_seconds = time.perf_counter() - start
    return chapters_written, elapsed_seconds


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
            help="Base URL of the TTS server (default: http://localhost:8000 for openai-speech)",
        ),
    ] = "",
    voice_backend: Annotated[
        str,
        typer.Option(
            "--voice-backend",
            help=(
                f"TTS backend to use: {' | '.join(_VOICE_BACKENDS)}. "
                "'openai-speech' targets POST /v1/audio/speech "
                "(Orpheus-FastAPI, Kokoro-FastAPI, …)."
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
    heading_tone: Annotated[
        str,
        typer.Option(
            "--heading-tone",
            help="Optional style instruction for heading tags (h1-h6).",
        ),
    ] = "",
    paragraph_tone: Annotated[
        str,
        typer.Option(
            "--paragraph-tone",
            help="Optional style instruction for paragraph/list tags.",
        ),
    ] = "",
    paragraph_pause_ms: Annotated[
        int,
        typer.Option(
            "--paragraph-pause-ms",
            min=0,
            max=10000,
            help="Silence inserted between consecutive paragraph-like blocks.",
        ),
    ] = 700,
    spool_temp_chunks: Annotated[
        bool,
        typer.Option(
            "--spool-temp-chunks/--no-spool-temp-chunks",
            help="Write per-block audio chunks to temp files before final merge to reduce memory usage.",
        ),
    ] = True,
    output_format: Annotated[
        str,
        typer.Option(
            "--output-format",
            "--chapter-format",
            help="Chapter output format: wav or mp3.",
        ),
    ] = "wav",
    reset_progress: Annotated[
        bool,
        typer.Option(
            "--reset-progress/--no-reset-progress",
            help="Reset resume index and temp paragraph chunks in the output directory before generation.",
        ),
    ] = False,
) -> None:
    """Generate an audiobook from an EPUB using a dedicated TTS model/backend."""
    command = _build_command(
        input_path=in_path,
        output_path=out_path,
        voice_model=voice_model,
        voice_backend=voice_backend,
        voice_base_url=voice_base_url,
        voice=voice,
        log_level=log_level,
        workers=workers,
        stream=stream,
        heading_tone=heading_tone,
        paragraph_tone=paragraph_tone,
        paragraph_pause_ms=paragraph_pause_ms,
        spool_temp_chunks=spool_temp_chunks,
        output_format=output_format,
        reset_progress=reset_progress,
    )
    configure_logging(command.log_level)
    audio_settings = _build_audio_settings(command)

    logger.info(
        "Starting audiobook generation | in=%s backend=%s model=%s url=%s voice=%s out=%s",
        command.input_path,
        command.voice_backend,
        command.voice_model,
        command.voice_base_url,
        command.voice or "(default)",
        command.output_path,
    )
    chapters_written, elapsed = _run_generation(command, audio_settings)
    duration_hms = _duration_hms(elapsed)

    logger.info(
        "Audiobook generation finished | chapters_written=%s out=%s in %s",
        chapters_written,
        command.output_path,
        duration_hms,
    )
    _render_summary(command.output_path, chapters_written, elapsed)

    raise typer.Exit(code=0)
