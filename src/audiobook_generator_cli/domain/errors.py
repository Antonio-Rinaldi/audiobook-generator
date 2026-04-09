from __future__ import annotations


class AudiobookGeneratorError(Exception):
    """Base domain error for audiobook generation workflows."""


# Backward-compatibility alias for older imports.
EpubTranslateError = AudiobookGeneratorError


class ValidationError(AudiobookGeneratorError):
    """Input validation error."""


class EpubReadError(AudiobookGeneratorError):
    """EPUB read/unpack/parse error."""


class EpubWriteError(AudiobookGeneratorError):
    """EPUB write/pack error."""


class AudioGenerationError(AudiobookGeneratorError):
    """Base error for audio generation provider failures."""


# Backward-compatibility alias for older imports.
TranslationError = AudioGenerationError


class RetryableTranslationError(AudioGenerationError):
    """Retryable request error (transient)."""


class NonRetryableTranslationError(AudioGenerationError):
    """Non-retryable request error."""
