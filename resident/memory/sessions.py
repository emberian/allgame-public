"""Conversation sessions: one per room, kept warm while the room is active.

A session holds the Agent SDK session id for its room, so consecutive turns
resume the same conversation (and reuse its prompt cache) instead of
rebuilding context each time. After SESSION_TTL of quiet it ends; ended
sessions are parked in a graveyard so the session-notes writer can summarize
them before they are forgotten.
"""

import logging
import threading
import time
from dataclasses import dataclass, field

from resident.config import SESSION_MAX_COUNT, SESSION_TTL

logger = logging.getLogger("sessions")


@dataclass
class Session:
    stream: str
    topic: str
    sdk_session_id: str = ""
    created: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    participants: set = field(default_factory=set)
    last_seen_msg_id: int = 0          # newest message already in context
    last_msg_ts: float = 0.0           # for "N hours since the last message"
    transcript: list = field(default_factory=list)   # [{role, text}] for notes
    sent_word_counts: list = field(default_factory=list)

    def touch(self):
        self.last_active = time.time()

    @property
    def idle(self) -> float:
        return time.time() - self.last_active


class Sessions:
    def __init__(self):
        self._by_room: dict[tuple[str, str], Session] = {}
        self._ended: list[Session] = []
        self._lock = threading.Lock()

    def get(self, stream: str, topic: str) -> Session | None:
        with self._lock:
            s = self._by_room.get((stream, topic))
            if s and s.idle > SESSION_TTL:
                self._ended.append(self._by_room.pop((stream, topic)))
                return None
            return s

    def get_or_create(self, stream: str, topic: str) -> tuple[Session, bool]:
        s = self.get(stream, topic)
        if s:
            return s, False
        with self._lock:
            while len(self._by_room) >= SESSION_MAX_COUNT:
                oldest = min(self._by_room, key=lambda k: self._by_room[k].last_active)
                self._ended.append(self._by_room.pop(oldest))
            s = self._by_room[(stream, topic)] = Session(stream, topic)
        logger.info(f"Session created: #{stream}>{topic}")
        return s, True

    def end(self, stream: str, topic: str):
        with self._lock:
            s = self._by_room.pop((stream, topic), None)
            if s:
                self._ended.append(s)

    def drain_ended(self) -> list[Session]:
        """Expire idle sessions and hand back everything that has ended."""
        with self._lock:
            for k in [k for k, s in self._by_room.items() if s.idle > SESSION_TTL]:
                self._ended.append(self._by_room.pop(k))
            out, self._ended = self._ended, []
        return out
