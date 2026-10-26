"""Small helpers shared across the harness."""

import re
import time
from datetime import datetime


def safe_filename(name: str) -> str:
    """A string usable as one path component."""
    s = re.sub(r"[^\w.\- ]+", "_", name or "").strip().replace(" ", "_")
    return s[:120] or "_"


def _scrub_terms() -> list[str]:
    try:
        from resident import profile
        return [t.lower() for t in profile.current().prompt.scrub_terms]
    except RuntimeError:
        return []


def withheld(text: str) -> bool:
    """True if the text carries a term the profile keeps from the model."""
    low = (text or "").lower()
    return any(t in low for t in _scrub_terms())


def masked(text: str) -> str:
    """The text with the profile's withheld terms blanked out."""
    import re
    for t in _scrub_terms():
        text = re.sub(re.escape(t), "[…]", text, flags=re.IGNORECASE)
    return text


def tail(text: str, max_chars: int, what: str = "the file") -> str:
    """The end of a long text, cut at a line boundary and saying so — a cut
    mid-sentence reads as a broken memory, not a window onto a longer one."""
    if not max_chars or len(text) <= max_chars:
        return text
    cut = text[-max_chars:]
    nl = cut.find("\n")
    if 0 <= nl < len(cut) - 1:
        cut = cut[nl + 1:]
    return f"[… the earlier part of {what} isn't shown here; read_state_file has all of it]\n{cut}"


def head_and_tail(text: str, head: int, tail_chars: int, what: str = "the file") -> str:
    """The start (who or what the file is about) and the recent end of a
    long text, each cut at a line, with a marker between them."""
    if len(text) <= head + tail_chars:
        return text
    start = text[:head].rsplit("\n", 1)[0]
    end = text[-tail_chars:]
    end = end[end.find("\n") + 1:] if "\n" in end else end
    return (f"{start}\n[… the middle of {what} isn't shown here; read it with "
            f"your tools …]\n{end}")


def room_file(stream: str, topic: str) -> str:
    """One filename per room, for files kept per (stream, topic)."""
    return f"{safe_filename(stream)}__{safe_filename(topic)}"


def fmt_log_time(ts: float) -> str:
    """Short local timestamp for transcript lines: 'Oct 13 21:05'."""
    return datetime.fromtimestamp(ts).strftime("%b %d %H:%M")


def fmt_now(ts: float | None = None) -> str:
    """'Mon 2026-10-13 21:05' — the clock a turn is told."""
    return datetime.fromtimestamp(ts or time.time()).strftime("%a %Y-%m-%d %H:%M")


def human_gap(seconds: float) -> str:
    """'3 minutes', '2 hours', '4 days'."""
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            n = int(seconds // size)
            return f"{n} {unit}{'' if n == 1 else 's'}"
    return f"{int(seconds)} seconds"
