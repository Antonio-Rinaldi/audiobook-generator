from __future__ import annotations


class EpubTranslateError(Exception):
    """Base error for this application."""


class ValidationError(EpubTranslateError):
    """Input validation error."""


class EpubReadError(EpubTranslateError):
    """EPUB read/unpack/parse error."""


class EpubWriteError(EpubTranslateError):
    """EPUB write/pack error."""


class TranslationError(EpubTranslateError):
    """Request error (may be retryable)."""


class RetryableTranslationError(TranslationError):
    """Retryable request error (transient)."""


class NonRetryableTranslationError(TranslationError):
    """Non-retryable request error."""
