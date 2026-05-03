"""TTS provider adapters and transport helpers."""

from audiobook_generator_cli.infrastructure.llm.openai_speech_audio_generator import (
    OpenAISpeechAudioGenerator,
)

__all__ = ["OpenAISpeechAudioGenerator"]