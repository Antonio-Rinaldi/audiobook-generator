# Architecture

## Current Architecture Overview

The project follows a clean-architecture layering pattern with three declared layers: `domain`, `application`, and
`infrastructure`. The structure is mostly sound, but several violations exist.

### Layer Boundary Violations

| Violation        | File                                             | Line           | Description                                                                                                                                                   |
|------------------|--------------------------------------------------|----------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|
| DI violation     | `cli.py`                                         | 59–61, 176–179 | CLI directly instantiates `ZipEpubRepository` and `OpenAISpeechAudioGenerator`; no injection point                                                            |
| ISP violation    | `domain/ports.py`                                | 17–19          | `EpubRepositoryPort.save()` never called by application layer; forces all implementations to provide a dead method                                            |
| Misplaced types  | `application/services/audiobook_orchestrator.py` | 76–135         | `NarrationBlock`, `ChapterJob`, `PreparedChapter` are application-layer value objects that live in the orchestrator file instead of a dedicated models module |
| Misplaced type   | `application/services/audiobook_orchestrator.py` | 219–278        | `ProgressIndex` is a stateful persistence component mixed into the orchestrator file                                                                          |
| Missing constant | `domain/models.py`                               | 19             | `AudioSettings.base_url` default disagrees with two other defaults for the same concept                                                                       |

### Current Dependency Graph

```
main.py
  └── cli.py
        ├── application/services/audiobook_orchestrator.py   (correct)
        ├── infrastructure/epub/epub_repository.py            (VIOLATION: direct import)
        ├── infrastructure/llm/openai_speech_audio_generator.py  (VIOLATION: direct import)
        └── infrastructure/logging/logger_factory.py

application/services/audiobook_orchestrator.py
  ├── domain/models.py
  ├── domain/ports.py                                         (correct)
  ├── infrastructure/logging/logger_factory.py               (acceptable: cross-cutting concern)
  └── pydub / lxml                                            (infrastructure leaking into application)

infrastructure/epub/epub_repository.py
  ├── domain/errors.py
  ├── domain/models.py
  ├── domain/ports.py
  └── infrastructure/logging/logger_factory.py

infrastructure/llm/openai_speech_audio_generator.py
  ├── domain/errors.py
  ├── domain/models.py
  ├── domain/ports.py
  └── requests
```

---

## Proposed Architecture

### Clean Dependency Graph (after enhancements)

```
main.py  ← composition root: instantiates infrastructure, injects into CLI handler
  └── cli.py  ← accepts injected ports, never imports infrastructure
        └── application/services/audiobook_orchestrator.py
              ├── application/models.py         (NarrationBlock, ChapterJob, PreparedChapter)
              ├── application/progress.py       (ProgressIndex)
              ├── application/merge.py          (audio merge utilities)
              ├── application/text.py           (text extraction + normalization)
              ├── domain/models.py
              ├── domain/ports.py
              └── infrastructure/logging/logger_factory.py

domain/  ← no imports from application or infrastructure
application/  ← imports from domain only (plus logging cross-cut)
infrastructure/  ← imports from domain only
main.py  ← imports from all layers (composition root)
```

### Proposed Folder / Module Structure

```
src/audiobook_generator_cli/
│
├── __init__.py                        # version constant
├── main.py                            # composition root: wires infrastructure, calls CLI
│
├── domain/
│   ├── __init__.py
│   ├── constants.py                   # NEW: _DEFAULT_TTS_BASE_URL and other shared constants
│   ├── errors.py                      # AudiobookGeneratorError hierarchy (aliases removed)
│   ├── models.py                      # ChapterDocument, AudioSettings, AudioRequest, AudioResponse
│   └── ports.py                       # EpubRepositoryPort (load only), AudioGeneratorPort
│
├── application/
│   ├── __init__.py
│   ├── models.py                      # NEW: NarrationBlock, ChapterJob, PreparedChapter
│   ├── progress.py                    # NEW: ProgressIndex (frozen dataclass + persistence)
│   ├── text.py                        # NEW: _extract_narration_blocks, _normalise_block_text, etc.
│   ├── merge.py                       # NEW: _merge_temp_chunks, _concat_wav_bytes relocated here
│   └── services/
│       ├── __init__.py
│       └── audiobook_orchestrator.py  # TRIMMED: orchestration logic only (~200 lines)
│
└── infrastructure/
    ├── __init__.py
    ├── epub/
    │   ├── __init__.py
    │   └── epub_repository.py         # ZipEpubRepository (save() removed)
    ├── llm/
    │   ├── __init__.py
    │   └── openai_speech_audio_generator.py
    └── logging/
        ├── __init__.py
        └── logger_factory.py
```

### New and Modified Domain Models

```python
# domain/constants.py
_DEFAULT_TTS_BASE_URL: str = "http://localhost:8000"


# domain/models.py
@dataclass(frozen=True)
class AudioSettings:
    model: str
    base_url: str = _DEFAULT_TTS_BASE_URL  # was three different defaults
    voice: str = ""
    heading_tone: str = ""
    paragraph_tone: str = ""
    paragraph_pause_ms: int = 700
    chapter_format: str = "wav"
    # spool_temp_chunks REMOVED — dead field


# domain/errors.py  (aliases removed)
class AudiobookGeneratorError(Exception): ...


class ValidationError(AudiobookGeneratorError): ...


class EpubReadError(AudiobookGeneratorError): ...


class EpubWriteError(AudiobookGeneratorError): ...


class AudioGenerationError(AudiobookGeneratorError): ...


class RetryableTranslationError(AudioGenerationError): ...


class NonRetryableTranslationError(AudioGenerationError): ...
```

### New Port / Interface Protocols

```python
# domain/ports.py  (save() removed from EpubRepositoryPort)
class EpubRepositoryPort(Protocol):
    def load(self, input_path: Path) -> EpubBook: ...


class AudioGeneratorPort(Protocol):
    def generate(self, request: AudioRequest, stream: bool = False) -> AudioResponse: ...
```

### Application Layer Changes

```python
# application/models.py
@dataclass(frozen=True)
class NarrationBlock:
    tag: str
    text: str

    @property
    def is_heading(self) -> bool: ...


@dataclass(frozen=True)
class ChapterJob:
    index: int
    total: int
    chapter: ChapterDocument
    audiobook_dir: Path
    settings: AudioSettings
    progress: "ProgressIndex"
    stream: bool

    @property
    def label(self) -> str: ...

    @property
    def chapter_key(self) -> str: ...

    @property
    def output_format(self) -> str: ...

    @property
    def output_path(self) -> Path: ...

    @property
    def temp_dir(self) -> Path: ...


@dataclass(frozen=True)
class PreparedChapter:
    job: ChapterJob
    blocks: list[NarrationBlock]
    completed_blocks: int
    existing_chunks: int


# application/progress.py
@dataclass(frozen=True)
class ProgressIndex:
    path: Path
    lock: Lock

    def get_chapter(self, chapter_key: str) -> dict[str, object]: ...

    def upsert_chapter_progress(self, chapter_key: str, ...) -> None: ...


# application/text.py
def extract_narration_blocks(xhtml_bytes: bytes) -> list[NarrationBlock]: ...


# raises EpubReadError on XMLSyntaxError (was: silent empty list)

# application/merge.py
def merge_temp_chunks(
        block_audio_files: list[tuple[NarrationBlock, Path]],
        out_file: Path,
        paragraph_pause_ms: int,
        output_format: str,
) -> None: ...
```

### Infrastructure Adapter Changes

```python
# infrastructure/epub/epub_repository.py
@dataclass(frozen=True)
class ZipEpubRepository(EpubRepositoryPort):
    def load(self, input_path: Path) -> EpubBook: ...
    # save() REMOVED


# infrastructure/llm/openai_speech_audio_generator.py
@dataclass(frozen=True)
class OpenAISpeechAudioGenerator(AudioGeneratorPort):
    base_url: str = _DEFAULT_TTS_BASE_URL  # was "http://localhost:5005"
    timeout_s: float = 6000.0
    max_chars_per_request: int = 3900
```

### CLI / Entry-Point Wiring Changes (Composition Root Pattern)

```python
# main.py  — composition root
def run() -> None:
    epub_repository = ZipEpubRepository()
    audio_generator = OpenAISpeechAudioGenerator(base_url=_resolve_tts_url(...))
    orchestrator = AudiobookOrchestrator(
        epub_repository=epub_repository,
        audio_generator=audio_generator,
    )
    app = build_typer_app(orchestrator)
    app()


# cli.py  — accepts orchestrator as parameter, imports no infrastructure
def build_typer_app(orchestrator: AudiobookOrchestrator) -> typer.Typer: ...
```

Note: The Typer CLI makes constructor injection awkward because Typer resolves the command function by reference. The
chosen approach is a module-level `_ORCHESTRATOR` variable set by `main.py` before the CLI runs, which avoids global
state visible to tests.

---

## Architecture Decision Records

### ADR-01: Remove `EpubRepositoryPort.save()` from the Port

**Context:** `save()` is defined on the port and implemented in `ZipEpubRepository` but is never called by the
application layer. The application only reads EPUBs.

**Decision:** Remove `save()` from the port. If write capability is needed in the future it can be introduced as a
separate `EpubWriterPort`.

**Consequences:** `ZipEpubRepository.save()` is also removed. Any external consumer depending on `save()` must provide
their own implementation. The test suite does not call `save()`, so no tests break.

### ADR-02: Raise `EpubReadError` on Chapter Parse Failure

**Context:** `_extract_narration_blocks` silently returns `[]` on `XMLSyntaxError`. The orchestrator then skips the
chapter, producing no output file and no error signal to the caller.

**Decision:** Raise `EpubReadError` wrapping the `XMLSyntaxError`. The orchestrator's `_process_chapter` re-raises it (
after the bare-except fix), which bubbles up to `generate()` which will log and count the chapter as failed.

**Consequences:** Callers see a failure instead of a silent skip. This is the correct behavior for corrupt input:
failing loudly is preferable to silently omitting audio for a chapter.

### ADR-03: Single `_DEFAULT_TTS_BASE_URL` Constant

**Context:** Three distinct default base URLs were scattered across `AudioSettings`, `OpenAISpeechAudioGenerator`, and
`_resolve_tts_url`. The disagreement causes subtle behavior: a user omitting `--voice-base-url` gets
`http://localhost:8000` from the CLI resolution, but constructing `OpenAISpeechAudioGenerator()` directly gives
`http://localhost:5005`.

**Decision:** Declare `_DEFAULT_TTS_BASE_URL = "http://localhost:8000"` in `domain/constants.py` and reference it from
all three locations.

**Consequences:** `http://localhost:8000` is the documented default in README and is consistent with the Orpheus-FastAPI
and Kokoro-FastAPI reference servers. The choice of 8000 over 5005 or 11434 matches the CLI's advertised default.

### ADR-04: Composition Root in `main.py`

**Context:** `cli.py` violates Dependency Inversion by directly importing and instantiating infrastructure classes.
Moving the instantiation into `main.py` (the entry point) keeps all infrastructure wiring in one place without requiring
a full DI container.

**Decision:** `main.py` becomes the composition root. It instantiates `ZipEpubRepository` and
`OpenAISpeechAudioGenerator`, constructs `AudiobookOrchestrator`, then passes the orchestrator into the CLI command via
a module-level variable set before `typer.run()`.

**Consequences:** `cli.py` becomes testable without importing infrastructure. The composition root is thin (< 20 lines).
The `--voice-backend` flag remains supported because the composition root reads it before selecting the concrete
implementation.

### ADR-05: `ProgressIndex` as `frozen=True` Dataclass

**Context:** `ProgressIndex` was declared as a mutable `@dataclass`. Its fields (`path` and `lock`) should never change
after construction; the mutable declaration is misleading.

**Decision:** Change to `@dataclass(frozen=True)`. The `Lock` object is held by reference; freezing the dataclass
prevents reassignment of the `lock` field but does not prevent the lock from being acquired and released. The
persistence methods mutate the JSON file on disk, not the dataclass fields.

**Consequences:** mypy and static analysis correctly flag any attempt to reassign `progress.path` or `progress.lock`
after construction.