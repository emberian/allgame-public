"""The floor: at most one resident composing a reply in a room at a time.

When several residents watch the same room, one message wakes all of them,
and without coordination they all answer at once — not longer replies,
parallel ones. The floor is a lease on "I am composing here", shared through
files every resident on the machine can see (SHARED_DIR/floor).

Three rules:

1. TAKE IT AFTER DECIDING, BEFORE SPENDING. A resident takes the floor once
   its judge has said yes but before the expensive turn, so one that loses
   stands down for free.

2. A LEASE, NOT A LOCK. The holder renews it while it types; a crashed
   process stops renewing and the lease expires in FLOOR_TTL. Nothing to
   clean up.

3. FAIRNESS. Strict first-come lets the fastest judge win every contested
   message forever. So everyone waits a short random backoff first, plus a
   penalty proportional to their SHARE of the room's recent turns (CSMA/CA,
   for the same reason it exists in networking). Share, not "spoke last":
   penalizing only the last speaker lets the two fastest residents alternate
   forever and starves the third.

   Being NAMED trumps all of it: a resident asked directly skips the
   backoff, and if it still loses the race it answers anyway (see
   resident.py) — two replies are better than silence from the one who was
   asked.

Not a model-facing tool: a rule the model must remember to follow is a rule
it will sometimes forget.
"""

import fcntl
import json
import logging
import os
import random
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from resident.config import FLOOR_TTL

logger = logging.getLogger("floor")

SHARED_DIR = Path(os.environ.get("RESIDENT_SHARED_DIR", Path.home() / ".resident"))
FLOOR_DIR = SHARED_DIR / "floor"
BACKOFF_BASE = 1.2              # jitter everyone pays, seconds
RECENT_SPEAKER_PENALTY = 4.0    # scaled by your share of recent turns
HISTORY = 6                     # recent holders a room remembers


def same(a: str | None, b: str | None) -> bool:
    """Name equality that survives case and diacritics ('Zoë' == 'zoe')."""
    if not a or not b:
        return False

    def fold(s):
        s = unicodedata.normalize("NFKD", s).casefold()
        return ("".join(c for c in s if ord(c) < 128) or s).strip()
    return fold(a) == fold(b)


def _key(stream: str, topic: str) -> str:
    raw = f"{stream}__{topic}"
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in raw)[:120]


def _path(stream, topic) -> Path:
    return FLOOR_DIR / f"{_key(stream, topic)}.json"


@contextmanager
def _locked(stream, topic):
    FLOOR_DIR.mkdir(parents=True, exist_ok=True)
    with open(FLOOR_DIR / f".{_key(stream, topic)}.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _read(stream, topic) -> dict | None:
    try:
        return json.loads(_path(stream, topic).read_text())
    except (OSError, ValueError):
        return None


def _write(stream, topic, data: dict):
    p = _path(stream, topic)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, p)


def _live(lease) -> bool:
    return bool(lease) and lease.get("held") and time.time() - lease.get("at", 0) < FLOOR_TTL


def holder(stream: str, topic: str) -> dict | None:
    lease = _read(stream, topic)
    return lease if _live(lease) else None


def backoff_for(share: float) -> float:
    return random.uniform(0, BACKOFF_BASE) + RECENT_SPEAKER_PENALTY * share


def acquire(stream: str, topic: str, who: str, *, trigger_id=None,
            addressed: bool = False) -> tuple[bool, dict | None]:
    """Try to take the floor: (got_it, current_holder_if_not).

    The backoff is slept OUTSIDE the lock, so waiting never blocks anyone.
    The share is read from the lease file itself, so it survives restarts
    and every resident sees the same history."""
    if addressed:
        share = 0.0
    else:
        recent = (_read(stream, topic) or {}).get("recent") or []
        share = sum(1 for r in recent if same(r, who)) / len(recent) if recent else 0.0
        time.sleep(backoff_for(share))
    with _locked(stream, topic):
        lease = _read(stream, topic)
        if _live(lease) and not same(lease.get("who"), who):
            return False, lease
        now = time.time()
        mine = {"who": who, "at": now, "taken_at": now, "held": True,
                "trigger": trigger_id,
                "recent": (((lease or {}).get("recent") or []) + [who])[-HISTORY:],
                "since": datetime.now(timezone.utc).isoformat()}
        _write(stream, topic, mine)
        return True, mine


def renew(stream: str, topic: str, who: str) -> bool:
    """Extend our lease; False once we no longer hold it. A released lease
    keeps the holder's name (that is the fairness history), so `held` must
    be checked, or renewal succeeds on a lease already given back."""
    with _locked(stream, topic):
        lease = _read(stream, topic)
        if not lease or not lease.get("held") or not same(lease.get("who"), who):
            return False
        lease["at"] = time.time()
        _write(stream, topic, lease)
        return True


def release(stream: str, topic: str, who: str):
    """Give the floor back. Safe to call when we don't hold it."""
    with _locked(stream, topic):
        lease = _read(stream, topic)
        if lease and lease.get("held") and same(lease.get("who"), who):
            held_for = time.time() - lease.get("taken_at", lease["at"])
            lease["held"] = False
            lease["at"] = time.time()
            _write(stream, topic, lease)
            logger.info(f"Floor released in #{stream}>{topic} by {who} after {held_for:.0f}s")
