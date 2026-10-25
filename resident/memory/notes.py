"""Session notes: when a conversation ends, the resident gets a note about it.

A session ends after the profile's session lifetime of quiet (or when it is evicted). A
background thread collects ended sessions and has the memory model write a
short first-person note — who was there, what happened, what was promised,
what is still open — appended to sessions/<room>.md. A cold start in that
room loads the tail of that file, so the next conversation can pick up where
this one left off.

If the model call fails, a plain stub is written instead: a note that says
"something happened here, with these people" beats a silent gap.
"""

import logging
import threading
import time
from datetime import datetime

from resident import llm
from resident.util import room_file

logger = logging.getLogger("notes")

CHECK_EVERY_S = 60
TRANSCRIPT_CHARS = 60_000   # the newest part of a long session

SYSTEM = """You write a short memory note for an AI resident of a group chat, \
in its own voice (first person), to its future self. It will read this the \
next time it joins this room, with no other memory of this conversation.

Write 3-8 plain sentences: who was there, what happened, anything it said it \
would do, and anything left open. Concrete names and specifics over mood. No \
headings, no lists, no preamble."""


def note_for(session) -> str:
    """The note text for one ended session (model-written, or a stub)."""
    lines = [f"{t['role']}: {t['text']}" for t in session.transcript if t.get("text")]
    transcript = "\n\n".join(lines)[-TRANSCRIPT_CHARS:]
    who = ", ".join(sorted(p for p in session.participants if p)) or "nobody named"
    try:
        return llm.complete(
            f"The conversation, in #{session.stream} > {session.topic} "
            f"(with {who}):\n\n{transcript}", system=SYSTEM).strip()
    except Exception as e:
        logger.warning(f"Session note for #{session.stream}>{session.topic} "
                       f"fell back to a stub: {e}")
        return (f"(No note could be written for this conversation. It was with "
                f"{who}, {len(session.transcript)} exchanges long.)")


def write(state, session):
    """Append the note for one ended session; skip sessions with no talk."""
    if not any(t.get("role") == "room" for t in session.transcript):
        return
    text = note_for(session)
    stamp = datetime.fromtimestamp(session.last_active).strftime("%Y-%m-%d %H:%M")
    state.append_file(f"sessions/{room_file(session.stream, session.topic)}.md",
                      f"## {stamp}\n{text}\n\n")
    logger.info(f"Session note written for #{session.stream}>{session.topic}")


def start(resident):
    def loop():
        while True:
            time.sleep(CHECK_EVERY_S)
            for s in resident.sessions.drain_ended():
                try:
                    write(resident.state, s)
                except Exception as e:
                    logger.warning(f"Session note failed: {e}")
    threading.Thread(target=loop, daemon=True, name="notes").start()
