# audiobook-generator-cli

Generate a per-chapter audiobook from an EPUB by extracting chapter text nodes and calling
an external TTS backend.

Output:

- a folder of per-chapter audio files (`--out`)

## Install (dev)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

## Requirements

- Python 3.9+
- For audiobook generation:
    - `openai-speech` backend (default): Orpheus-FastAPI or any OpenAI-compatible
      `/v1/audio/speech` server (default: `http://localhost:5005`)
    - `ollama` backend: an Ollama model that returns audio via `/api/generate`

## Usage

### Audiobook generation (Orpheus-FastAPI / OpenAI-speech backend)

```bash
audiobook-generate \
  --in ./sample1.italiano.epub \
  --out ./sample1_audiobook/ \
  --voice-model orpheus \
  --voice-backend openai-speech \
  --voice tara \
  --voice-base-url http://localhost:5005
```

### Audiobook generation (Ollama backend)

```bash
audiobook-generate \
  --in ./sample1.italiano.epub \
  --out ./sample1_audiobook/ \
  --voice-model outetts \
  --voice-backend ollama \
  --voice-base-url http://localhost:11434
```

## Flags

| Flag               | Default                                                                     | Description                                                                                 |
|--------------------|-----------------------------------------------------------------------------|---------------------------------------------------------------------------------------------|
| `--in`             | *(required)*                                                                | Input EPUB path (must exist).                                                               |
| `--out`            | `<in_stem>_audiobook/`                                                      | Directory for audio files; one file per non-empty chapter. Created automatically if absent. |
| `--voice-model`    | *(required)*                                                                | TTS model name (e.g. `orpheus`, `outetts`)                                                  |
| `--voice-backend`  | `openai-speech`                                                             | TTS backend: `openai-speech` (`POST /v1/audio/speech`) or `ollama` (`/api/generate`)        |
| `--voice-base-url` | `http://localhost:5005` (openai-speech) / `http://localhost:11434` (ollama) | Base URL of the TTS server. Overrides per-backend default.                                  |
| `--voice`          | *(backend default)*                                                         | Voice name passed to backend (e.g. `tara`, `leo`, `leah` for Orpheus).                      |
| `--log-level`      | `INFO`                                                                      | Logging verbosity (`INFO` or `DEBUG`)                                                       |

## Logging

- Default level is `INFO`
- Set `--log-level DEBUG` for detailed runtime diagnostics

## Exit codes

- `0`: success
- `1`: fatal error

## TODO

Send Qwen-TTS paragraphs and then merge instead of sending full chapter
