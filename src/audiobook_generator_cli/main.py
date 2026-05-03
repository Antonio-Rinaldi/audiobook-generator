from __future__ import annotations

import audiobook_generator_cli.cli as cli_module
from audiobook_generator_cli.application.services.audiobook_orchestrator import (
    AudiobookOrchestrator,
)
from audiobook_generator_cli.infrastructure.epub.epub_repository import ZipEpubRepository
from audiobook_generator_cli.infrastructure.llm.openai_speech_audio_generator import (
    OpenAISpeechAudioGenerator,
)


def run() -> None:
    """Run Typer CLI application with all registered commands."""
    epub_repository = ZipEpubRepository()
    audio_generator = OpenAISpeechAudioGenerator()
    cli_module._audio_generator = audio_generator
    cli_module._orchestrator = AudiobookOrchestrator(
        epub_repository=epub_repository,
        audio_generator=audio_generator,
    )
    cli_module.app()