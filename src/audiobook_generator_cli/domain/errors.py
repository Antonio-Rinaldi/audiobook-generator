from __future__ import annotations


class AudiobookGeneratorError(Exception):
    """Base domain error for audiobook generation workflows."""


class ValidationError(AudiobookGeneratorError):
    """Input validation error."""


class EpubReadError(AudiobookGeneratorError):
    """EPUB read/unpack/parse error."""


class EpubWriteError(AudiobookGeneratorError):
    """EPUB write/pack error."""


class AudioGenerationError(AudiobookGeneratorError):
    """Base error for audio generation provider failures."""


class RetryableTranslationError(AudioGenerationError):
    """Retryable request error (transient)."""


class NonRetryableTranslationError(AudioGenerationError):
    """Non-retryable request error."""