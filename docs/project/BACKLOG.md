# Product Backlog

## Priority Matrix

| Story ID | Title                                                       | Epic   | Points | Priority    | Dependencies                       | Status |
|----------|-------------------------------------------------------------|--------|--------|-------------|------------------------------------|--------|
| E6S1     | Add detect_punctuation_hints to application/text.py         | Epic 6 | 2      | P2 High     | E3S3                               | ✅ Done |
| E6S2     | Extend _instruction_for_block with punctuation hint clauses | Epic 6 | 2      | P2 High     | E6S1                               | ✅ Done |
| E6S3     | Unit tests for detect_punctuation_hints (all flag paths)    | Epic 6 | 2      | P2 High     | E6S1                               | ✅ Done |
| E6S4     | Unit tests for _instruction_for_block punctuation clauses   | Epic 6 | 2      | P2 High     | E6S2                               | ✅ Done |
| E1S1     | Fix ruff lint errors across src and tests                   | Epic 1 | 2      | P1 Critical | —                                  | ✅ Done |
| E1S2     | Fix mypy strict errors (stubs + annotations)                | Epic 1 | 2      | P1 Critical | —                                  | ✅ Done |
| E1S3     | Declare audioop-lts for Python 3.13 and fix test collection | Epic 1 | 1      | P1 Critical | —                                  | ✅ Done |
| E1S4     | Remove dead backward-compatibility aliases                  | Epic 1 | 1      | P1 Critical | E1S1                               | ✅ Done |
| E1S5     | Remove spool_temp_chunks dead control flow                  | Epic 1 | 1      | P1 Critical | E1S1                               | ✅ Done |
| E1S6     | Make ProgressIndex frozen                                   | Epic 1 | 1      | P1 Critical | E1S1                               | ✅ Done |
| E2S1     | Raise EpubReadError on chapter parse failure                | Epic 2 | 2      | P1 Critical | E1S1, E1S2                         | ✅ Done |
| E2S2     | Replace bare except swallow in _process_chapter             | Epic 2 | 2      | P1 Critical | E2S1                               | ✅ Done |
| E3S1     | Extract application-layer models module                     | Epic 3 | 3      | P2 High     | E1S1, E1S2                         | ✅ Done |
| E3S2     | Extract ProgressIndex into application/progress.py          | Epic 3 | 2      | P2 High     | E3S1                               | ✅ Done |
| E3S3     | Extract text utilities into application/text.py             | Epic 3 | 2      | P2 High     | E3S1, E2S1                         | ✅ Done |
| E3S4     | Extract merge utilities into application/merge.py           | Epic 3 | 2      | P2 High     | E3S1                               | ✅ Done |
| E3S5     | Align TTS base URL to single named constant                 | Epic 3 | 1      | P2 High     | E1S1                               | ✅ Done |
| E3S6     | Remove save() from EpubRepositoryPort                       | Epic 3 | 1      | P2 High     | E1S1                               | ✅ Done |
| E3S7     | Composition root in main.py (DI fix)                        | Epic 3 | 3      | P2 High     | E3S1, E3S2, E3S3, E3S4, E3S5, E3S6 | ✅ Done |
| E5S1     | Remove sys.path manipulation from conftest                  | Epic 5 | 1      | P2 High     | —                                  | ✅ Done |
| E5S2     | Fix test private import — SemanticTextChunker unit tests    | Epic 5 | 2      | P2 High     | E1S1                               | ✅ Done |
| E5S3     | Add integration test with minimal EPUB fixture              | Epic 5 | 3      | P2 High     | E3S7                               | ✅ Done |
| E5S4     | Add pytest-cov with 80% threshold                           | Epic 5 | 1      | P3 Medium   | E5S2, E5S3                         | ✅ Done |
| E5S5     | Add GitHub Actions CI pipeline                              | Epic 5 | 2      | P3 Medium   | E1S1, E1S2, E5S4                   | ✅ Done |

---

## Epic 1: Code Quality and SOLID Refactoring

### E1S1 — Fix ruff lint errors across src and tests

**As a** developer,
**I want** `ruff check src/ tests/` to exit with zero errors,
**so that** the primary lint gate passes and all subsequent stories start from a clean baseline.

**Acceptance Criteria:**

- `ruff check src/ tests/` exits with code 0. (All files, 47 current errors)
- Import blocks in `src/audiobook_generator_cli/application/services/__init__.py:3` are sorted.
- Import blocks in `src/audiobook_generator_cli/application/services/audiobook_orchestrator.py:1-18` are sorted (lxml
  placed in third-party group after pydub, not separated).
- Import blocks in `tests/unit/test_openai_speech_audio_generator.py:1-13` are sorted (stdlib before third-party).
- All lines in `src/` and `tests/` are ≤ 100 characters.
- No ruff autofixes introduce behaviour changes.

**Story Points:** 2
**Priority:** P1 Critical
**Dependencies:** None

---

### E1S2 — Fix mypy strict errors (stubs + annotations)

**As a** developer,
**I want** `mypy src/` under `--strict` mode to exit with zero errors,
**so that** the type-checking gate passes and type correctness is enforced.

**Acceptance Criteria:**

- `lxml-stubs` added to `[project.optional-dependencies] dev` in `pyproject.toml`.
- `types-requests` added to `[project.optional-dependencies] dev` in `pyproject.toml`.
- Both stub packages installed in dev environment.
- `ProgressIndex._load_unlocked` return type is `dict[str, object]` not bare `dict` (`audiobook_orchestrator.py:226`).
- `ProgressIndex._save_unlocked` parameter type is `dict[str, object]` not bare `dict` (
  `audiobook_orchestrator.py:242`).
- `ProgressIndex.get_chapter` return type is `dict[str, object]` not bare `dict` (`audiobook_orchestrator.py:249`).
- `AudiobookOrchestrator._is_chapter_done` parameter type is `dict[str, object]` not bare `dict` (
  `audiobook_orchestrator.py:354`).
- `OpenAISpeechAudioGenerator._extract_audio_bytes` returns `bytes` with an explicit cast removing the `no-any-return`
  error (`openai_speech_audio_generator.py:206`).
- `mypy src/` exits with code 0.

**Story Points:** 2
**Priority:** P1 Critical
**Dependencies:** None

---

### E1S3 — Declare audioop-lts for Python 3.13 and fix test collection

**As a** developer,
**I want** the test suite to collect and run on Python 3.13 without `ModuleNotFoundError`,
**so that** the test gate passes on the project's declared minimum Python version.

**Acceptance Criteria:**

- `pyproject.toml` declares `audioop-lts>=0.2.1; python_version >= "3.13"` in `[project.dependencies]`.
- `pytest -q tests/` collects all test files without import errors on Python 3.13.
- All 15 existing tests pass.

**Story Points:** 1
**Priority:** P1 Critical
**Dependencies:** None

---

### E1S4 — Remove dead backward-compatibility aliases

**As a** developer,
**I want** dead code aliases removed from the error hierarchy,
**so that** the error module only contains types that are actually in use.

**Acceptance Criteria:**

- `EpubTranslateError` removed from `domain/errors.py:8` (no callers in `src/` or `tests/`).
- `TranslationError` removed from `domain/errors.py:29` (no callers in `src/` or `tests/`).
- `ruff check` and `mypy` still pass after removal.
- No test references either alias.

**Story Points:** 1
**Priority:** P1 Critical
**Dependencies:** E1S1

---

### E1S5 — Remove spool_temp_chunks dead control flow

**As a** developer,
**I want** the never-enforced `spool_temp_chunks` flag removed from the codebase,
**so that** the CLI surface matches actual behavior and there is no misleading field.

**Acceptance Criteria:**

- `spool_temp_chunks` field removed from `domain/models.py:24` (`AudioSettings`).
- `spool_temp_chunks` field removed from `cli.py:48` (`GenerateCommand`).
- `--spool-temp-chunks / --no-spool-temp-chunks` CLI option removed from `cli.py:265-270`.
- `spool_temp_chunks` mapping removed from `_build_audio_settings` in `cli.py:149`.
- `spool_temp_chunks=False` kwarg in `tests/unit/test_audiobook_orchestrator.py:143` removed.
- All tests still pass after removal.

**Story Points:** 1
**Priority:** P1 Critical
**Dependencies:** E1S1

---

### E1S6 — Make ProgressIndex frozen

**As a** developer,
**I want** `ProgressIndex` declared as `@dataclass(frozen=True)`,
**so that** static analysis and runtime both enforce that the checkpoint's path and lock cannot be reassigned after
construction.

**Acceptance Criteria:**

- `ProgressIndex` at `audiobook_orchestrator.py:219` is declared `@dataclass(frozen=True)`.
- mypy accepts the frozen declaration without errors.
- All tests still pass.

**Story Points:** 1
**Priority:** P1 Critical
**Dependencies:** E1S1

---

## Epic 2: Domain-Specific Quality Improvements

### E2S1 — Raise EpubReadError on chapter parse failure

**As a** pipeline operator,
**I want** a corrupted chapter XHTML to raise a domain error rather than silently produce no audio,
**so that** corrupt input is detected and the failure is surfaced to the caller.

**Acceptance Criteria:**

- `_extract_narration_blocks` in `audiobook_orchestrator.py:145-149` raises `EpubReadError(str(exc))` wrapping the
  `etree.XMLSyntaxError` instead of returning `[]`.
- A unit test in `tests/unit/test_extract_text.py` asserts that malformed XHTML raises `EpubReadError`.
- `ruff check` and `mypy` pass after the change.

**Story Points:** 2
**Priority:** P1 Critical
**Dependencies:** E1S1, E1S2

---

### E2S2 — Replace bare except swallow in `_process_chapter`

**As a** pipeline operator,
**I want** chapter failures to propagate structured domain errors,
**so that** the caller can distinguish retryable TTS errors from non-retryable ones and from unrecoverable
infrastructure errors.

**Acceptance Criteria:**

- The `except Exception as exc:` block at `audiobook_orchestrator.py:466` is replaced with: catch
  `AudiobookGeneratorError` subclasses and re-raise them; wrap any other unexpected exception in
  `AudiobookGeneratorError`.
- A unit test verifies that a `RetryableTranslationError` raised by `FakeAudio.generate` propagates out of
  `_process_chapter` (not swallowed).
- `ruff check` and `mypy` pass.

**Story Points:** 2
**Priority:** P1 Critical
**Dependencies:** E2S1

---

## Epic 3: Architecture Improvements

### E3S1 — Extract application-layer models module

**As a** developer,
**I want** `NarrationBlock`, `ChapterJob`, and `PreparedChapter` to live in `application/models.py`,
**so that** the orchestrator module is responsible only for orchestration logic.

**Acceptance Criteria:**

- `application/models.py` is created containing `NarrationBlock`, `ChapterJob`, `PreparedChapter`.
- `audiobook_orchestrator.py` imports these from `application.models` instead of defining them locally.
- All existing tests still pass.
- `ruff check` and `mypy` pass.

**Story Points:** 3
**Priority:** P2 High
**Dependencies:** E1S1, E1S2

---

### E3S2 — Extract ProgressIndex into application/progress.py

**As a** developer,
**I want** `ProgressIndex` to live in `application/progress.py`,
**so that** checkpoint persistence is a cohesive, independently testable component.

**Acceptance Criteria:**

- `application/progress.py` is created containing `ProgressIndex` (as `frozen=True` per E1S6).
- `audiobook_orchestrator.py` imports `ProgressIndex` from `application.progress`.
- All existing tests still pass.
- `ruff check` and `mypy` pass.

**Story Points:** 2
**Priority:** P2 High
**Dependencies:** E3S1

---

### E3S3 — Extract text utilities into application/text.py

**As a** developer,
**I want** all text extraction and normalization functions to live in `application/text.py`,
**so that** text processing is independently testable and the orchestrator imports a clean function rather than defining
utilities inline.

**Acceptance Criteria:**

- `application/text.py` is created containing: `_local_tag_name`, `_normalise_block_text`, `_has_spoken_text`,
  `_strip_inline_tags_for_tts`, `_is_heading_tag`, `extract_narration_blocks` (public, raises `EpubReadError`).
- `_HEADING_TAGS` and `_NARRATABLE_TAGS` constants move to `application/text.py`.
- `audiobook_orchestrator.py` imports `extract_narration_blocks` from `application.text`.
- The backward-compatible `_extract_paragraphs` and `_extract_narration_blocks` shims in orchestrator are removed;
  existing tests that import `_extract_paragraphs` are updated to import from `application.text`.
- All tests pass. `ruff check` and `mypy` pass.

**Story Points:** 2
**Priority:** P2 High
**Dependencies:** E3S1, E2S1

---

### E3S4 — Extract merge utilities into application/merge.py

**As a** developer,
**I want** audio merge logic to live in `application/merge.py`,
**so that** merge behavior is independently testable and the orchestrator imports a clean function.

**Acceptance Criteria:**

- `application/merge.py` is created containing: `merge_temp_chunks`, helper constants for chunk path resolution.
- `audiobook_orchestrator.py` imports `merge_temp_chunks` from `application.merge`.
- `_TEMP_CHUNKS_DIR` and `_CHAPTER_XML_DIR` constants remain accessible from the orchestrator (moved or re-exported).
- All tests pass. `ruff check` and `mypy` pass.

**Story Points:** 2
**Priority:** P2 High
**Dependencies:** E3S1

---

### E3S5 — Align TTS base URL to single named constant

**As a** developer,
**I want** a single `_DEFAULT_TTS_BASE_URL` constant used everywhere a default TTS URL is needed,
**so that** the default is consistent regardless of which code path constructs the generator.

**Acceptance Criteria:**

- `domain/constants.py` is created with `_DEFAULT_TTS_BASE_URL: str = "http://localhost:8000"`.
- `AudioSettings.base_url` default in `domain/models.py:19` references `_DEFAULT_TTS_BASE_URL`.
- `OpenAISpeechAudioGenerator.base_url` default in `openai_speech_audio_generator.py:168` references
  `_DEFAULT_TTS_BASE_URL`.
- `_resolve_tts_url` in `cli.py:94` uses `_DEFAULT_TTS_BASE_URL` instead of the string literal.
- All tests pass. `ruff check` and `mypy` pass.

**Story Points:** 1
**Priority:** P2 High
**Dependencies:** E1S1

---

### E3S6 — Remove save() from EpubRepositoryPort

**As a** developer,
**I want** the unused `save()` method removed from `EpubRepositoryPort` and `ZipEpubRepository`,
**so that** the port reflects only the capability the application actually uses.

**Acceptance Criteria:**

- `save()` removed from `EpubRepositoryPort` at `domain/ports.py:17-19`.
- `save()` removed from `ZipEpubRepository` at `epub_repository.py:73-80`.
- `FakeRepo.save()` removed from `tests/unit/test_audiobook_orchestrator.py:22-23`.
- All tests pass. `ruff check` and `mypy` pass.

**Story Points:** 1
**Priority:** P2 High
**Dependencies:** E1S1

---

### E3S7 — Composition root in main.py (DI fix)

**As a** developer,
**I want** `main.py` to instantiate all infrastructure and inject them into the CLI handler,
**so that** `cli.py` does not import any concrete infrastructure class and the Dependency Inversion Principle is
satisfied.

**Acceptance Criteria:**

- `cli.py` contains zero imports from `audiobook_generator_cli.infrastructure.*`.
- `main.py` imports `ZipEpubRepository` and `OpenAISpeechAudioGenerator` and constructs `AudiobookOrchestrator`.
- The `--voice-backend` flag in CLI still functions; `main.py` composition root reads it to select the implementation.
- All tests pass (test double injection still works via `AudiobookOrchestrator` constructor).
- `ruff check` and `mypy` pass.

**Story Points:** 3
**Priority:** P2 High
**Dependencies:** E3S1, E3S2, E3S3, E3S4, E3S5, E3S6

---

## Epic 4: Spec / Standard Compliance

No spec-compliance stories identified beyond what is already covered by E2S1 (parse error handling) and E3S6 (port
correctness). EPUB OPF spine ordering is acknowledged as out of scope in SPEC.md.

---

## Epic 5: Testing and CI Infrastructure

### E5S1 — Remove sys.path manipulation from conftest

**As a** developer,
**I want** `tests/conftest.py` to contain no manual `sys.path` manipulation,
**so that** the test suite relies on the installed editable package and conftest.py is not a source of path-resolution
surprises.

**Acceptance Criteria:**

- `tests/conftest.py` lines 6-8 (`sys.path.insert`) are removed.
- `pytest -q tests/` still collects and passes all tests without the manipulation.

**Story Points:** 1
**Priority:** P2 High
**Dependencies:** None

---

### E5S2 — Fix test private import — SemanticTextChunker unit tests

**As a** developer,
**I want** `SemanticTextChunker` to be tested directly through its public `split()` method rather than via the private
`_split_text_semantic` helper,
**so that** tests are decoupled from internal implementation details.

**Acceptance Criteria:**

- `tests/unit/test_openai_speech_audio_generator.py` no longer imports `_split_text_semantic`.
- A new `tests/unit/test_semantic_text_chunker.py` covers at minimum:
    - `split` with text under `max_chars` (single chunk).
    - `split` with multi-paragraph text where paragraphs fit individually but not together.
    - `split` with a single overly-long sentence (sliced into hard chunks).
    - `split` with empty string (returns `[]`).
    - `split` respects `max_chars` for all returned chunks.
- `ruff check` and `mypy` pass on the new file.

**Story Points:** 2
**Priority:** P2 High
**Dependencies:** E1S1

---

### E5S3 — Add integration test with minimal EPUB fixture

**As a** developer,
**I want** an integration test that runs the full pipeline end-to-end with a real minimal EPUB ZIP and a fake TTS
adapter,
**so that** regressions in the file-read → extract → synthesize → merge → write pipeline are caught automatically.

**Acceptance Criteria:**

- `tests/integration/` directory exists with `__init__.py` and `test_pipeline_integration.py`.
- `tests/integration/fixtures/minimal.epub` is a valid EPUB ZIP containing one XHTML chapter with a `<p>` element.
- The integration test:
    - Creates a `ZipEpubRepository` (real implementation, not a fake).
    - Uses a `FakeAudioGenerator` that returns valid WAV bytes.
    - Calls `AudiobookOrchestrator.generate()` with the fixture EPUB.
    - Asserts that one `.wav` file is written in the output directory.
    - Asserts that `.audiobook_progress.json` exists and `completed=true` for the chapter.
- All tests pass. `ruff check` and `mypy` pass.

**Story Points:** 3
**Priority:** P2 High
**Dependencies:** E3S7

---

### E5S4 — Add pytest-cov with 80% threshold

**As a** developer,
**I want** coverage measurement enforced in the test command,
**so that** coverage regressions are caught automatically.

**Acceptance Criteria:**

- `pytest-cov>=4.0.0` added to `[project.optional-dependencies] dev` in `pyproject.toml`.
- `[tool.pytest.ini_options]` in `pyproject.toml` sets
  `addopts = "-q --cov=audiobook_generator_cli --cov-report=term-missing --cov-fail-under=80"`.
- `pytest -q tests/` exits with code 0 with coverage ≥ 80%.

**Story Points:** 1
**Priority:** P3 Medium
**Dependencies:** E5S2, E5S3

---

### E5S5 — Add GitHub Actions CI pipeline

**As a** developer,
**I want** a GitHub Actions workflow that runs lint, type-checking, and tests on every push to `main` and on every pull
request,
**so that** quality gate failures are surfaced before code merges.

**Acceptance Criteria:**

- `.github/workflows/ci.yml` exists.
- Workflow triggers on `push` to `main` and `pull_request` targeting `main`.
- Workflow has three jobs: `lint` (ruff check), `typecheck` (mypy src/), `test` (pytest with coverage).
- Each job uses `python-version: ["3.13"]` and installs `.[dev]`.
- All jobs must pass for the workflow to succeed.
- The workflow file passes `ruff check` (YAML is not checked by ruff, but the file must be syntactically valid GitHub
  Actions YAML).

---

## Epic 6: Punctuation-Aware TTS Tone Generation

### E6S1 — Add detect_punctuation_hints to application/text.py

**As a** TTS pipeline operator,
**I want** typographic cues in block text to be detected automatically,
**so that** downstream instruction builders can produce richer, more natural TTS prompts without manual annotation.

**Acceptance Criteria:**

- `PunctuationHints` frozen dataclass added to `application/text.py` with fields: `has_dialogue`,
  `dialogue_ends_with_comma`, `has_ellipsis`, `has_em_dash`, `has_exclamation_in_dialogue`,
  `has_question_in_dialogue`, `has_colon_before_quote`.
- `detect_punctuation_hints(text: str) -> PunctuationHints` public function added to `application/text.py`.
- Detects `«…»`, `"…"`, and `"…"` as dialogue spans for `has_dialogue`.
- `dialogue_ends_with_comma` detects a comma immediately before the closing quote/guillemet.
- `has_ellipsis` detects the `…` character.
- `has_em_dash` detects `—` or `–`.
- `has_exclamation_in_dialogue` / `has_question_in_dialogue` are true only when `!`/`?` appear inside a dialogue span.
- `has_colon_before_quote` detects `:` followed by an opening quote/guillemet.
- `ruff check`, `mypy src/`, and `pytest` all pass.

**Story Points:** 2
**Priority:** P2 High
**Dependencies:** E3S3

---

### E6S2 — Extend _instruction_for_block with punctuation hint clauses

**As a** TTS pipeline operator,
**I want** the narration instruction to include natural performance guidance derived from typographic cues,
**so that** the TTS model reads dialogue, hesitations, interruptions, and questions with appropriate expression.

**Acceptance Criteria:**

- `_instruction_for_block` calls `detect_punctuation_hints` on `block.text` for non-heading blocks.
- One instruction clause added per active hint, using natural descriptive language (no mechanical commands).
- Clauses are additive: the base instruction and any user-supplied tone are always preserved.
- Headings bypass hint analysis entirely (heading path returns before hint detection).
- `AudioRequest`, `AudioResponse`, `AudioSettings`, and `AudioGeneratorPort` signatures are unchanged.
- No new external dependencies introduced.
- `ruff check`, `mypy src/`, and `pytest` all pass.

**Story Points:** 2
**Priority:** P2 High
**Dependencies:** E6S1

---

### E6S3 — Unit tests for detect_punctuation_hints (all flag paths)

**As a** developer,
**I want** every `PunctuationHints` flag to be covered by a dedicated true and false test case,
**so that** regressions in hint detection are caught immediately.

**Acceptance Criteria:**

- `tests/unit/test_punctuation_hints.py` contains tests for each of the 7 hint flags (true and false paths).
- Frozen enforcement test confirms that assigning to a hint field raises `AttributeError` or `TypeError`.
- All tests pass. `ruff check` and `mypy` pass.

**Story Points:** 2
**Priority:** P2 High
**Dependencies:** E6S1

---

### E6S4 — Unit tests for _instruction_for_block punctuation clauses

**As a** developer,
**I want** `_instruction_for_block` to have a test for every punctuation-derived clause it can emit,
**so that** the instruction composition logic is independently verified.

**Acceptance Criteria:**

- Tests confirm each hint flag produces the corresponding clause keyword in the instruction text.
- Test confirms headings do not receive hint-derived clauses.
- Test confirms base instruction and user paragraph tone are preserved alongside hint clauses.
- All tests pass. `ruff check` and `mypy` pass.

**Story Points:** 2
**Priority:** P2 High
**Dependencies:** E6S2

**Story Points:** 2
**Priority:** P3 Medium
**Dependencies:** E1S1, E1S2, E5S4