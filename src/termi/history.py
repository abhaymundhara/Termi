"""Persistent command history and bookmarks for Termi."""
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .config import BOOKMARKS_FILE, HISTORY_FILE, _ensure_config_dir


@dataclass
class HistoryEntry:
    timestamp: float
    query: str
    command: str
    mode: str = "oneshot"
    model: str = ""
    exit_code: Optional[int] = None
    cwd: str = ""
    bookmarked: bool = False


class History:
    """JSONL-backed command history."""

    def __init__(self, limit: int = 500):
        _ensure_config_dir()
        self._limit = limit
        self._entries: List[HistoryEntry] = []
        self._load()

    def _load(self) -> None:
        if not HISTORY_FILE.exists():
            return
        try:
            lines = HISTORY_FILE.read_text(encoding="utf-8").strip().splitlines()
            for line in lines[-self._limit:]:
                try:
                    obj = json.loads(line)
                    self._entries.append(HistoryEntry(**obj))
                except (json.JSONDecodeError, TypeError):
                    continue
        except OSError:
            pass

    def add(self, query: str, command: str, mode: str = "oneshot",
            model: str = "", exit_code: Optional[int] = None, cwd: str = "") -> None:
        entry = HistoryEntry(
            timestamp=time.time(),
            query=query,
            command=command,
            mode=mode,
            model=model,
            exit_code=exit_code,
            cwd=cwd,
        )
        self._entries.append(entry)
        if len(self._entries) > self._limit:
            self._entries = self._entries[-self._limit:]
        self._append_to_file(entry)

    def _append_to_file(self, entry: HistoryEntry) -> None:
        try:
            with HISTORY_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry)) + "\n")
        except OSError:
            pass

    def search(self, query: str) -> List[HistoryEntry]:
        q = query.lower()
        return [e for e in reversed(self._entries)
                if q in e.query.lower() or q in e.command.lower()]

    def recent(self, n: int = 20) -> List[HistoryEntry]:
        return list(reversed(self._entries[-n:]))

    def clear(self) -> None:
        self._entries.clear()
        try:
            HISTORY_FILE.write_text("", encoding="utf-8")
        except OSError:
            pass

    @property
    def entries(self) -> List[HistoryEntry]:
        return list(self._entries)


class Bookmarks:
    """JSON-backed command bookmarks."""

    def __init__(self):
        _ensure_config_dir()
        self._bookmarks: Dict[str, Dict] = {}
        self._load()

    def _load(self) -> None:
        if not BOOKMARKS_FILE.exists():
            return
        try:
            self._bookmarks = json.loads(
                BOOKMARKS_FILE.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError):
            pass

    def _save(self) -> None:
        try:
            BOOKMARKS_FILE.write_text(
                json.dumps(self._bookmarks, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def add(self, name: str, command: str, description: str = "") -> None:
        self._bookmarks[name] = {
            "command": command,
            "description": description,
            "created": time.time(),
        }
        self._save()

    def remove(self, name: str) -> bool:
        if name in self._bookmarks:
            del self._bookmarks[name]
            self._save()
            return True
        return False

    def get(self, name: str) -> Optional[Dict]:
        return self._bookmarks.get(name)

    def list_all(self) -> Dict[str, Dict]:
        return dict(self._bookmarks)
