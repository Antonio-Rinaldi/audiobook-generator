from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from audiobook_generator_cli.infrastructure.logging.logger_factory import create_logger

logger = create_logger(__name__)


@dataclass(frozen=True)
class ProgressIndex:
    """Thread-safe checkpoint persistence for chapter and paragraph progress."""

    path: Path
    lock: Lock

    def _load_unlocked(self) -> dict[str, object]:
        """Load persisted progress payload without acquiring outer lock."""
        if not self.path.exists():
            return {"version": 1, "chapters": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning(
                "Progress index is invalid JSON, resetting | path=%s", self.path
            )
            return {"version": 1, "chapters": {}}
        if not isinstance(payload, dict):
            return {"version": 1, "chapters": {}}
        chapters = payload.get("chapters")
        if not isinstance(chapters, dict):
            payload["chapters"] = {}
        return payload

    def _save_unlocked(self, payload: dict[str, object]) -> None:
        """Persist progress payload atomically without acquiring outer lock."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        temp_path.replace(self.path)

    def get_chapter(self, chapter_key: str) -> dict[str, object]:
        """Return shallow copy of one chapter state from progress index."""
        with self.lock:
            payload = self._load_unlocked()
            chapters = payload.get("chapters", {})
            if not isinstance(chapters, dict):
                return {}
            chapter_state = chapters.get(chapter_key)
            if not isinstance(chapter_state, dict):
                return {}
            return chapter_state.copy()

    def upsert_chapter_progress(
        self,
        chapter_key: str,
        chapter_path: str,
        total_blocks: int,
        completed_blocks: int,
        output_file: str,
        completed: bool,
    ) -> None:
        """Insert or update chapter progress state in checkpoint file."""
        with self.lock:
            payload = self._load_unlocked()
            chapters = payload.get("chapters")
            if not isinstance(chapters, dict):
                chapters = {}
                payload["chapters"] = chapters
            chapters[chapter_key] = {
                "chapter_path": chapter_path,
                "total_blocks": total_blocks,
                "completed_blocks": completed_blocks,
                "completed": completed,
                "output_file": output_file,
            }
            self._save_unlocked(payload)