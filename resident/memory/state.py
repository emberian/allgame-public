"""The resident's state directory: its memory, as plain files.

    <state_dir>/
      scratchpad.md            working notes, loaded every turn
      journal.md               longer reflection; the tail is loaded
      people/<name>.md         what it knows about people
      channels/<stream>/<topic>.jsonl   every message it has seen, forever
      sessions/                notes written when a conversation ends
      sysadmin/                messages to the human who runs it

The resident reads and writes these with its own tools. Channel logs are
append-only: history is never rotated away; what gets bounded is how much is
LOADED into a turn, never what is kept.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from resident.util import fmt_log_time, safe_filename, withheld

SEED_FILES = {
    "scratchpad.md": "# Scratchpad\n\nWorking notes. Loaded into every turn.\n",
    "journal.md": "# Journal\n",
}
SUBDIRS = ("people", "channels", "sessions", "sysadmin")


class PathOutsideState(ValueError):
    pass


class StateManager:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        for d in SUBDIRS:
            (self.root / d).mkdir(exist_ok=True)
        for name, content in SEED_FILES.items():
            if not (self.root / name).exists():
                (self.root / name).write_text(content)
        self._log_lock = threading.Lock()

    # ---- files ---------------------------------------------------------

    def path(self, relative: str) -> Path:
        """Resolve a state-relative path, refusing anything outside the dir."""
        p = (self.root / relative).resolve()
        try:
            p.relative_to(self.root.resolve())
        except ValueError:
            raise PathOutsideState(f"{relative} is outside the state directory")
        return p

    def read_file(self, relative: str) -> str | None:
        p = self.path(relative)
        return p.read_text() if p.is_file() else None

    def write_file(self, relative: str, content: str):
        p = self.path(relative)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def append_file(self, relative: str, content: str):
        p = self.path(relative)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            f.write(content)

    # ---- channel logs ----------------------------------------------------

    def log_path(self, stream: str, topic: str) -> Path:
        return self.root / "channels" / safe_filename(stream) / f"{safe_filename(topic)}.jsonl"

    def log_message(self, stream: str, topic: str, sender: str, content: str,
                    ts: float | None = None, msg_id: int | None = None):
        p = self.log_path(stream, topic)
        p.parent.mkdir(parents=True, exist_ok=True)
        row = {"msg_id": msg_id, "ts": datetime.fromtimestamp(
                   ts or datetime.now().timestamp(), timezone.utc).isoformat(),
               "topic": topic, "sender": sender, "content": content}
        with self._log_lock, open(p, "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _rows(self, stream: str, topic: str) -> list[dict]:
        p = self.log_path(stream, topic)
        if not p.exists():
            return []
        out = []
        for line in p.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not withheld(row.get("content", "")):
                out.append(row)
        return out

    @staticmethod
    def _fmt(row: dict) -> str:
        ts = datetime.fromisoformat(row["ts"]).timestamp()
        return f"[{fmt_log_time(ts)}] {row['sender']}: {row['content']}"

    def _tail(self, rows: list[dict], max_chars: int) -> str:
        """The newest rows that fit in max_chars, oldest first."""
        out, used = [], 0
        for row in reversed(rows):
            line = self._fmt(row)
            if used + len(line) + 1 > max_chars and out:
                break
            out.append(line)
            used += len(line) + 1
        return "\n".join(reversed(out))

    def logged_ids(self, stream: str, topic: str) -> set:
        """Message ids already in a room's log (withheld ones included)."""
        p = self.log_path(stream, topic)
        if not p.exists():
            return set()
        ids = set()
        for line in p.read_text().splitlines():
            try:
                ids.add(json.loads(line).get("msg_id"))
            except ValueError:
                continue
        return ids

    def topic_history(self, stream: str, topic: str, max_chars: int) -> str:
        """Recent conversation in one room, for a cold start or a birth."""
        return self._tail(self._rows(stream, topic), max_chars)

    def stream_activity(self, stream: str, max_chars: int,
                        exclude_topic: str | None = None) -> str:
        """Recent activity across a stream's topics, newest last."""
        d = self.root / "channels" / safe_filename(stream)
        if not d.exists():
            return ""
        rows = []
        for f in d.glob("*.jsonl"):
            for line in f.read_text().splitlines()[-200:]:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("topic") != exclude_topic and not withheld(r.get("content", "")):
                    rows.append(r)
        rows.sort(key=lambda r: r["ts"])
        out, used = [], 0
        for r in reversed(rows):
            line = f"[{r['topic']}] " + self._fmt(r)
            if used + len(line) > max_chars and out:
                break
            out.append(line)
            used += len(line) + 1
        return "\n".join(reversed(out))

    def messages_since(self, stream: str, topic: str, since_id: int,
                       exclude_ids: set, exclude_sender: str,
                       max_chars: int) -> str:
        """What was said in a room after `since_id` that the resident did not
        answer — context for a warm session that skipped some messages."""
        rows = [r for r in self._rows(stream, topic)
                if (r.get("msg_id") or 0) > since_id
                and r.get("msg_id") not in exclude_ids
                and exclude_sender not in (r.get("sender") or "").lower()]
        return self._tail(rows, max_chars)

    def previous_message_time(self, stream: str, topic: str,
                              exclude_ids: set | None = None) -> float | None:
        for r in reversed(self._rows(stream, topic)):
            if exclude_ids and r.get("msg_id") in exclude_ids:
                continue
            return datetime.fromisoformat(r["ts"]).timestamp()
        return None
