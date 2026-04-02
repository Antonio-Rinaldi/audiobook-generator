from __future__ import annotations

import json
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
class OllamaAudioGenerator(AudioGeneratorPort):
    """Text-to-speech generator backed by an Ollama TTS-capable model.

    Ollama does not have a dedicated TTS endpoint; instead we call
    ``/api/generate`` with a prompt that instructs the model to produce audio.
    For models that return base64-encoded audio in the ``audio`` or ``images``
    field we decode that; otherwise we treat ``response`` as raw UTF-8 bytes.
    """

    base_url: str = "http://localhost:11434"
    timeout_s: float = 300.0

    def generate(self, request: AudioRequest, stream: bool = False) -> AudioResponse:
        logger.debug(
            "Calling Ollama TTS | model=%s text_len=%s",
            request.model,
            len(request.text),
        )

        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": request.model,
                    "prompt": request.text,
                    "stream": False,
                },
                timeout=self.timeout_s,
            )
        except requests.RequestException as exc:
            raise RetryableTranslationError(str(exc)) from exc

        if resp.status_code >= 500:
            raise RetryableTranslationError(f"Ollama TTS server error: {resp.status_code}")
        if resp.status_code >= 400:
            raise NonRetryableTranslationError(
                f"Ollama TTS request failed: {resp.status_code} {resp.text}"
            )

        try:
            payload = resp.json()
        except json.JSONDecodeError as exc:
            raise RetryableTranslationError("Invalid JSON from Ollama TTS") from exc

        # Some Ollama TTS models return base64-encoded audio in an "audio" key.
        import base64

        audio_b64: str | None = (
            payload.get("audio")
            # Older builds may embed audio as the first element of "images".
            or (payload.get("images") or [None])[0]
        )

        if audio_b64:
            try:
                audio_bytes = base64.b64decode(audio_b64)
                fmt = "wav"
            except Exception as exc:  # noqa: BLE001
                raise RetryableTranslationError(f"Could not decode base64 audio: {exc}") from exc
        else:
            # Fall back: use raw response text encoded as bytes.
            raw_text = (payload.get("response") or "").strip()
            if not raw_text:
                raise RetryableTranslationError("Empty response from Ollama TTS")
            audio_bytes = raw_text.encode()
            fmt = "wav"

        logger.debug(
            "Ollama TTS response received | model=%s bytes=%s",
            request.model,
            len(audio_bytes),
        )
        return AudioResponse(audio_bytes=audio_bytes, format=fmt)
