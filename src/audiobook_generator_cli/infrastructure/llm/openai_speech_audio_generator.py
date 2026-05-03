from __future__ import annotations

from dataclasses import dataclass

import requests

from audiobook_generator_cli.domain.constants import _DEFAULT_TTS_BASE_URL
from audiobook_generator_cli.domain.errors import (
    NonRetryableTranslationError,
    RetryableTranslationError,
)
from audiobook_generator_cli.domain.models import AudioRequest, AudioResponse
from audiobook_generator_cli.domain.ports import AudioGeneratorPort
from audiobook_generator_cli.infrastructure.logging.logger_factory import create_logger

logger = create_logger(__name__)

_DEFAULT_TIMEOUT_S: float = 6000.0


def _response_error_excerpt(resp: requests.Response, max_len: int = 200) -> str:
    """Extract a short response body excerpt for error diagnostics."""
    try:
        body = resp.content or b""
    except Exception:  # noqa: BLE001
        body = b""
    if not body:
        return ""
    return body.decode("utf-8", errors="replace")[:max_len]


@dataclass(frozen=True)
class OpenAISpeechAudioGenerator(AudioGeneratorPort):
    """Text-to-speech generator that calls ``/v1/audio/speech`` endpoint."""

    base_url: str = _DEFAULT_TTS_BASE_URL
    timeout_s: float = _DEFAULT_TIMEOUT_S

    @staticmethod
    def _build_payload(request: AudioRequest) -> dict[str, str]:
        """Build OpenAI-compatible speech payload."""
        optional_pairs = (
            ("voice", request.voice),
            ("instructions", request.instructions),
        )
        optional_payload = {key: value for key, value in optional_pairs if value}
        return {
            "model": request.model,
            "input": request.text,
            "response_format": "wav",
            **optional_payload,
        }

    def _speech_url(self, stream: bool) -> str:
        """Return endpoint URL for streaming or non-streaming speech requests."""
        suffix = "?stream=true" if stream else ""
        return f"{self.base_url}/v1/audio/speech{suffix}"

    def _send_tts_request(self, payload: dict[str, str], stream: bool) -> requests.Response:
        """Send speech request to backend and convert transport failures to retryable errors."""
        try:
            if stream:
                return requests.post(
                    self._speech_url(stream=True),
                    json=payload,
                    timeout=self.timeout_s,
                    stream=True,
                )
            return requests.post(
                self._speech_url(stream=False), json=payload, timeout=self.timeout_s
            )
        except requests.RequestException as exc:
            raise RetryableTranslationError(str(exc)) from exc

    @staticmethod
    def _extract_audio_bytes(resp: requests.Response, stream: bool) -> bytes:
        """Extract audio bytes from regular or streaming HTTP responses."""
        if stream:
            return b"".join(chunk for chunk in resp.iter_content(chunk_size=8192) if chunk)
        return bytes(resp.content)

    @staticmethod
    def _detect_output_format(resp: requests.Response) -> str:
        """Infer audio format from HTTP content-type header."""
        content_type = resp.headers.get("content-type", "audio/wav")
        return "mp3" if "mpeg" in content_type or "mp3" in content_type else "wav"

    @staticmethod
    def _validate_response(resp: requests.Response) -> None:
        """Validate HTTP response status and raise mapped domain errors."""
        if resp.status_code >= 500:
            raise RetryableTranslationError(f"TTS server error: {resp.status_code}")
        if resp.status_code >= 400:
            raise NonRetryableTranslationError(
                f"TTS request failed: {resp.status_code} {_response_error_excerpt(resp)}"
            )

    def generate(self, request: AudioRequest, stream: bool = False) -> AudioResponse:
        """Generate audio from text using OpenAI-speech TTS.

        The API handles text chunking and WAV assembly internally; this method
        makes a single HTTP call per invocation and returns the merged result.
        """
        logger.debug(
            "Calling OpenAI-speech TTS | model=%s voice=%s text_len=%s stream=%s",
            request.model,
            request.voice or "(default)",
            len(request.text),
            stream,
        )

        payload = self._build_payload(request)
        resp = self._send_tts_request(payload, stream=stream)
        self._validate_response(resp)
        audio_bytes = self._extract_audio_bytes(resp, stream=stream)
        if not audio_bytes:
            raise RetryableTranslationError("Empty audio response from TTS server")

        fmt = self._detect_output_format(resp)
        logger.debug(
            "OpenAI-speech TTS response received | model=%s bytes=%s fmt=%s stream=%s",
            request.model,
            len(audio_bytes),
            fmt,
            stream,
        )
        return AudioResponse(audio_bytes=audio_bytes, format=fmt)