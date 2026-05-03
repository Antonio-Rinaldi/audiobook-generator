# Feature Spec — Chapter Order in Generated Audio Files

## Problem

`AudiobookOrchestrator.generate()` processes chapters in parallel via `ThreadPoolExecutor`.
Chapter output files are currently named `{chapter_path_stem}.{format}` — e.g. `ch3.wav`.
Because the file system does not guarantee alphabetical matching of EPUB reading order,
audio players that sort by filename will play chapters out of sequence.

## Root Cause

`ChapterJob.output_path` (`application/models.py:64`) derives the filename solely from the
chapter's internal EPUB path stem, discarding the 1-based `index` field that already encodes
reading order.

## Solution

Prefix every chapter output filename with a zero-padded 3-digit index:

```
001_ch1.wav
002_chapter_two.wav
...
012_epilogue.wav
```

Three digits supports up to 999 chapters without sort collisions. Parallelism is unchanged —
workers still run concurrently, but each worker already knows its assigned `index` from the
`ChapterJob` it receives.

## Scope

**In scope**
- One-line change to `ChapterJob.output_path` property in `application/models.py`
- Update 4 test path assertions in `tests/unit/test_audiobook_orchestrator.py`
- Add a test verifying multi-chapter output files sort in EPUB reading order

**Out of scope**
- Resume-index format changes (chapter_key is the EPUB path, unaffected)
- Any change to parallelism, progress tracking, or merging logic

## Acceptance Criteria

1. `ChapterJob.output_path` returns `audiobook_dir / f"{index:03d}_{stem}.{format}"`.
2. A two-chapter EPUB produces `001_ch1.wav` and `002_ch2.wav` in the output directory.
3. All existing orchestrator tests pass with updated path assertions.
4. Quality gate: `pytest` ≥ 80 % coverage, `ruff` clean, `mypy` clean.