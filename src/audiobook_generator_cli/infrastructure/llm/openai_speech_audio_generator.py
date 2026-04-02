from __future__ import annotations

import io
import re
import wave
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

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")


def _response_error_excerpt(resp: requests.Response, max_len: int = 200) -> str:
    try:
        body = resp.content or b""
    except Exception:
        body = b""
    if not body:
        return ""
    return body.decode("utf-8", errors="replace")[:max_len]


def _split_text_semantic(text: str, max_chars: int) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n{2,}", cleaned) if p.strip()]
    chunks: list[str] = []
    current = ""

    def flush_current() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    def append_part(part: str) -> None:
        nonlocal current
        part = part.strip()
        if not part:
            return

        candidate = f"{current}\n\n{part}" if current else part
        if len(candidate) <= max_chars:
            current = candidate
            return

        flush_current()

        if len(part) <= max_chars:
            current = part
            return

        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(part) if s.strip()]
        if len(sentences) <= 1:
            for i in range(0, len(part), max_chars):
                slice_part = part[i : i + max_chars].strip()
                if slice_part:
                    chunks.append(slice_part)
            return

        sentence_acc = ""
        for sentence in sentences:
            sentence_candidate = f"{sentence_acc} {sentence}".strip() if sentence_acc else sentence
            if len(sentence_candidate) <= max_chars:
                sentence_acc = sentence_candidate
                continue

            if sentence_acc:
                chunks.append(sentence_acc.strip())
                sentence_acc = ""

            if len(sentence) <= max_chars:
                sentence_acc = sentence
            else:
                for i in range(0, len(sentence), max_chars):
                    slice_part = sentence[i : i + max_chars].strip()
                    if slice_part:
                        chunks.append(slice_part)

        if sentence_acc.strip():
            chunks.append(sentence_acc.strip())

    for paragraph in paragraphs:
        append_part(paragraph)

    flush_current()
    return chunks


def _concat_wav_bytes(parts: list[bytes]) -> bytes:
    if not parts:
        return b""
    if len(parts) == 1:
        return parts[0]

    frames: list[bytes] = []
    params: tuple[int, int, int, str, str] | None = None

    for item in parts:
        with wave.open(io.BytesIO(item), "rb") as reader:
            current_params = (
                reader.getnchannels(),
                reader.getsampwidth(),
                reader.getframerate(),
                reader.getcomptype(),
                reader.getcompname(),
            )
            if params is None:
                params = current_params
            elif params != current_params:
                raise RetryableTranslationError("Incompatible WAV chunks returned by TTS server")
            frames.append(reader.readframes(reader.getnframes()))

    if params is None:
        return b""

    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(params[0])
        writer.setsampwidth(params[1])
        writer.setframerate(params[2])
        writer.setcomptype(params[3], params[4])
        for frame in frames:
            writer.writeframes(frame)
    return out.getvalue()


@dataclass(frozen=True)
class OpenAISpeechAudioGenerator(AudioGeneratorPort):
    """Text-to-speech generator that calls ``/v1/audio/speech`` endpoint."""

    base_url: str = "http://localhost:5005"
    timeout_s: float = 6000.0
    max_chars_per_request: int = 3900

    def generate(
        self, request: AudioRequest, stream: bool = False
    ) -> AudioResponse:
        """
        Generate audio from text using OpenAI-speech TTS.
        If stream=True, server responses are consumed via chunked transfer.
        Returns AudioResponse with merged audio bytes in both modes.
        """
        logger.debug(
            "Calling OpenAI-speech TTS | model=%s voice=%s text_len=%s stream=%s",
            request.model,
            request.voice or "(default)",
            len(request.text),
            stream,
        )

        text_chunks = _split_text_semantic(request.text, self.max_chars_per_request)
        if not text_chunks:
            raise NonRetryableTranslationError("Empty text after preprocessing")

        audio_chunks: list[bytes] = []
        out_fmt = "wav"
        for idx, text_chunk in enumerate(text_chunks, start=1):
            payload: dict[str, str] = {
                "model": request.model,
                "input": text_chunk,
                "response_format": "wav",
            }
            if request.voice:
                payload["voice"] = request.voice
            try:
                if stream:
                    url = f"{self.base_url}/v1/audio/speech?stream=true"
                    resp = requests.post(url, json=payload, timeout=self.timeout_s, stream=True)
                else:
                    url = f"{self.base_url}/v1/audio/speech"
                    resp = requests.post(url, json=payload, timeout=self.timeout_s)
            except requests.RequestException as exc:
                raise RetryableTranslationError(str(exc)) from exc
            if resp.status_code >= 500:
                raise RetryableTranslationError(f"TTS server error: {resp.status_code}")
            if resp.status_code >= 400:
                raise NonRetryableTranslationError(
                    f"TTS request failed: {resp.status_code} {_response_error_excerpt(resp)}"
                )
            if stream:
                audio_bytes = b"".join(
                    chunk_bytes
                    for chunk_bytes in resp.iter_content(chunk_size=8192)
                    if chunk_bytes
                )
            else:
                audio_bytes = resp.content
            if not audio_bytes:
                raise RetryableTranslationError("Empty audio response from TTS server")
            content_type = resp.headers.get("content-type", "audio/wav")
            fmt = "mp3" if "mpeg" in content_type or "mp3" in content_type else "wav"
            out_fmt = fmt
            audio_chunks.append(audio_bytes)
            logger.debug(
                "OpenAI-speech TTS chunk received | chunk=%s/%s chars=%s bytes=%s fmt=%s stream=%s",
                idx,
                len(text_chunks),
                len(text_chunk),
                len(audio_bytes),
                fmt,
                stream,
            )
        if out_fmt == "wav":
            merged = _concat_wav_bytes(audio_chunks)
        else:
            merged = b"".join(audio_chunks)
        logger.debug(
            "OpenAI-speech TTS response merged | model=%s chunks=%s bytes=%s fmt=%s stream=%s",
            request.model,
            len(text_chunks),
            len(merged),
            out_fmt,
            stream,
        )
        return AudioResponse(audio_bytes=merged, format=out_fmt)
