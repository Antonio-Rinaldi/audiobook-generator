from __future__ import annotations

from dataclasses import dataclass

import requests

from audiobook_generator_cli.domain.errors import (
    NonRetryableTranslationError,
    RetryableTranslationError,
)
from audiobook_generator_cli.domain.models import AudioRequest, AudioResponse
from audiobook_generator_cli.domain.ports import AudioGeneratorPort
from audiobook_generator_cli.infrastructure.logging.logger_factory import create_logger

logger = create_logger(__name__)


@dataclass(frozen=True)
class OpenAISpeechAudioGenerator(AudioGeneratorPort):
    """Text-to-speech generator that calls ``/v1/audio/speech`` endpoint."""

    base_url: str = "http://localhost:5005"
    timeout_s: float = 600.0

    def generate(self, request: AudioRequest) -> AudioResponse:
        logger.debug(
            "Calling OpenAI-speech TTS | model=%s voice=%s text_len=%s",
            request.model,
            request.voice or "(default)",
            len(request.text),
        )

        payload: dict = {
            "model": request.model,
            "input": request.text,
        }
        if request.voice:
            payload["voice"] = request.voice

        try:
            resp = requests.post(
                f"{self.base_url}/v1/audio/speech",
                json=payload,
                timeout=self.timeout_s,
            )
        except requests.RequestException as exc:
            raise RetryableTranslationError(str(exc)) from exc

        if resp.status_code >= 500:
            raise RetryableTranslationError(f"TTS server error: {resp.status_code}")
        if resp.status_code >= 400:
            raise NonRetryableTranslationError(
                f"TTS request failed: {resp.status_code} {resp.text[:200]}"
            )

        audio_bytes = resp.content
        if not audio_bytes:
            raise RetryableTranslationError("Empty audio response from TTS server")

        # Detect format from Content-Type header; default to wav.
        content_type = resp.headers.get("content-type", "audio/wav")
        fmt = "mp3" if "mpeg" in content_type or "mp3" in content_type else "wav"

        logger.debug(
            "OpenAI-speech TTS response received | model=%s bytes=%s fmt=%s",
            request.model,
            len(audio_bytes),
            fmt,
        )
        return AudioResponse(audio_bytes=audio_bytes, format=fmt)
