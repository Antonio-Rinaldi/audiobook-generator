from __future__ import annotations

# Default base URL for OpenAI-compatible TTS servers.
# Used by AudioSettings, OpenAISpeechAudioGenerator, and the CLI URL resolver.
_DEFAULT_TTS_BASE_URL: str = "http://localhost:8000"