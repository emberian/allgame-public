"""Timers and watches: a resident waking itself up.

  set_timer      one-shot (in_minutes / at) or repeating (every_minutes): a
                 turn runs later in a room, carrying the resident's own note.
                 With `watch`, a repeating timer becomes a wake-loop that only
                 spends a turn when its room has news.
  list_timers / cancel_timer

Timers live in timers.json in the resident's state dir, so they survive
restarts; anything that came due while the resident was down fires shortly
after it comes back. A timer is CLAIMED (removed or rescheduled) before its
turn runs, so a crash mid-turn cannot fire it twice.

Every wake is a paid turn, so there is a cap on how many a resident may
hold, and repeats expire unless given an end.
"""

import fcntl
import json
import logging
import os
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from resident import profile
from resident.tools.registry import tool

logger = logging.getLogger("timers")

FILE = "timers.json"
MAX_ACTIVE = 15
MIN_SECONDS = 60          # the loop checks every 30s; a minute is the floor
MAX_AHEAD = 30 * 86400
DEFAULT_LIFETIME = 7 * 86400
CHECK_S = 30


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def _parse(s: str) -> float:
    dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.astimezone()).timestamp()


@contextmanager
def _timers(state):
    path, lock = state.root / FILE, open(state.root / (FILE + ".lock"), "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        try:
            box = {"t": json.loads(path.read_text()) if path.exists() else []}
        except ValueError:
            box = {"t": []}
        yield box
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(box["t"], indent=1, ensure_ascii=False))
        os.replace(tmp, path)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


@tool("timers", "set_timer",
      """Wake yourself up later: a turn runs in a room (default: this one)
      carrying your note, as if you'd left yourself a message. One-shot:
      in_minutes or at (ISO time). Repeating: every_minutes (from 1 minute;
      expires after 7 days unless until is given). Add watch=true to a
      repeating timer to make it wake you only when someone has posted in
      the room since the last check. At most 15 timers. Write the note so
      you will understand it cold.""",
      required=["note"], note={"type": "string"},
      in_minutes={"type": "number"}, at={"type": "string"},
      every_minutes={"type": "number"}, until={"type": "string"},
      watch={"type": "boolean"}, stream={"type": "string"}, topic={"type": "string"})
def set_timer(args, ctx):
    note = (args.get("note") or "").strip()
    stream = args.get("stream") or ctx.stream
    topic = args.get("topic") or ctx.topic
    if not note:
        return {"content": "A timer needs a note.", "is_error": True}
    if ctx.message.get("type") == "private" and not args.get("stream"):
        return {"content": "From a DM, give stream and topic explicitly.", "is_error": True}
    now = time.time()
    try:
        if args.get("every_minutes") is not None:
            every = int(float(args["every_minutes"]) * 60)
            if every < MIN_SECONDS:
                return {"content": "every_minutes must be at least 1.", "is_error": True}
            due = now + every
            until = _parse(args["until"]) if args.get("until") else now + DEFAULT_LIFETIME
        else:
            if args.get("watch"):
                return {"content": "A watch needs every_minutes.", "is_error": True}
            every, until = None, None
            due = _parse(args["at"]) if args.get("at") else \
                now + float(args["in_minutes"]) * 60 if args.get("in_minutes") is not None else None
            if due is None:
                return {"content": "Give in_minutes, at, or every_minutes.", "is_error": True}
            if not MIN_SECONDS <= due - now <= MAX_AHEAD:
                return {"content": "Between a minute and 30 days out.", "is_error": True}
    except (TypeError, ValueError) as e:
        return {"content": f"Could not read the time: {e}", "is_error": True}
    t = {"id": secrets.token_hex(3), "note": note, "stream": stream, "topic": topic,
         "due": due, "every": every, "until": until, "created": now, "fires": 0,
         "watch_since": now if args.get("watch") else None}
    with _timers(ctx.state) as box:
        if len(box["t"]) >= MAX_ACTIVE:
            return {"content": f"You have {MAX_ACTIVE} timers; cancel one first.",
                    "is_error": True}
        box["t"].append(t)
    kind = ("watching for news every" if t["watch_since"] else "repeating every") \
        + f" {every // 60} min" if every else "once"
    return {"content": f"Timer {t['id']}: first at {_iso(due)} in #{stream}>{topic}, {kind}."}


@tool("timers", "list_timers", "Your timers: id, next time, room, note.")
def list_timers(args, ctx):
    with _timers(ctx.state) as box:
        ts = sorted(box["t"], key=lambda t: t["due"])
    return {"content": "\n".join(
        f"{t['id']}  next {_iso(t['due'])[:16]}  #{t['stream']}>{t['topic']}  "
        f"[{'watch' if t.get('watch_since') else 'every ' + str(t['every'] // 60) + 'm' if t.get('every') else 'once'}]"
        f"\n    {t['note'][:200]}" for t in ts) or "No timers."}


@tool("timers", "cancel_timer", "Cancel a timer by id.", required=["id"], id={"type": "string"})
def cancel_timer(args, ctx):
    with _timers(ctx.state) as box:
        before = len(box["t"])
        box["t"] = [t for t in box["t"] if t["id"] != args["id"]]
        gone = before - len(box["t"])
    return {"content": f"Timer {args['id']} cancelled."} if gone else \
        {"content": f"No timer {args['id']}.", "is_error": True}


# ---- firing -----------------------------------------------------------------

def _news(state, t) -> str | None:
    """Messages in the watched room since the last check, by others."""
    me = profile.current().bot_name
    rows = [r for r in state._rows(t["stream"], t["topic"])
            if datetime.fromisoformat(r["ts"]).timestamp() > t["watch_since"]
            and me not in (r.get("sender") or "").lower()]
    return "\n".join(state._fmt(r) for r in rows[-20:]) if rows else None


def _prompt(t, news) -> str:
    p = (f"[TIMER {t['id']}] A timer you set yourself on {_iso(t['created'])[:16]} UTC — "
         f"not a message from anyone. Your note:\n\n{t['note']}\n")
    if news:
        p += f"\nIt woke you because the room has news since you last looked:\n{news}\n"
    return p + ("\nDo what you meant to do. Speaking is optional — if there's "
                "nothing worth saying, say nothing. If this timer no longer serves "
                f"you, cancel_timer('{t['id']}').")


def due_now(state) -> list[tuple[dict, str]]:
    now, out = time.time(), []
    with _timers(state) as box:
        keep = []
        for t in box["t"]:
            if t["due"] > now:
                keep.append(t)
                continue
            if t.get("until") and now > t["until"]:
                continue
            news = None
            if t.get("watch_since"):
                news = _news(state, t)
                t["watch_since"] = now
            if t.get("every"):
                t["due"] = now + t["every"]
                keep.append(t)
            if t.get("watch_since") and not news:
                continue
            t["fires"] += 1
            out.append((dict(t), _prompt(t, news)))
        box["t"] = keep
    return out


def start(state, run_turn):
    """run_turn(stream, topic, prompt) runs one turn in that room."""
    def loop():
        while True:
            time.sleep(CHECK_S)
            try:
                for t, prompt in due_now(state):
                    logger.info(f"Timer {t['id']} firing in #{t['stream']}>{t['topic']}")
                    try:
                        run_turn(t["stream"], t["topic"], prompt)
                    except Exception as e:
                        logger.warning(f"timer {t['id']} turn failed: {e}")
            except Exception as e:
                logger.warning(f"timer loop: {e}")
    threading.Thread(target=loop, daemon=True, name="timers").start()
