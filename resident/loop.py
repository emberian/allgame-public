"""The event loop: messages in, batched per room, handed to the resident.

Order on start-up matters:

  1. backfill — read each room's recent history into the logs, so the first
     turn has context;
  2. REGISTER the event queue;
  3. only then collect the backlog — messages that arrived while the
     resident was down — and queue it ahead of live events.

Collecting the backlog before registering (and answering it there) leaves
the resident deaf for as long as the backlog takes, and anything said in that
window is never delivered: the queue only records from the moment it exists.

Messages that pile up in a room while a turn is running are coalesced into
one turn rather than answered one by one.
"""

import logging
import sys
import time
from collections import deque

from resident import profile
from resident.config import BACKFILL_PER_ROOM, REPLAY_MAX_AGE_SECONDS
from resident.resident import room_of

logger = logging.getLogger("loop")

EVENTS = ["message", "typing"]


def backfill(resident):
    for stream in profile.current().chat.rooms:
        r = resident.chat.get_messages({
            "anchor": "newest", "num_before": BACKFILL_PER_ROOM, "num_after": 0,
            "narrow": [{"operator": "channel", "operand": stream}],
            "apply_markdown": False})
        for m in r.get("messages", []):
            if m["id"] not in resident._seen_ids:
                s, t = room_of(m)
                resident.state.log_message(s, t, m.get("sender_full_name", "?"),
                                           m.get("content", ""), m.get("timestamp"), m["id"])
                resident._seen_ids.add(m["id"])


def backlog(resident) -> list[dict]:
    """Unhandled messages from the downtime, oldest first. On a first boot
    there was no downtime: history is read, not owed."""
    if resident.handled.pristine:
        for mid in resident._seen_ids:
            resident.handled.mark(mid)
        logger.info("First boot: history marked read; listening from now")
        return []
    cutoff = time.time() - REPLAY_MAX_AGE_SECONDS
    found = {}
    narrows = [[{"operator": "channel", "operand": s}] for s in profile.current().chat.rooms]
    narrows.append([{"operator": "is", "operand": "private"}])
    for narrow in narrows:
        r = resident.chat.get_messages({"anchor": "newest", "num_before": 50,
                                        "num_after": 0, "narrow": narrow,
                                        "apply_markdown": False})
        for m in r.get("messages", []):
            if m["id"] in resident.handled or resident.bot_name in m.get("sender_full_name", "").lower():
                continue
            if m.get("timestamp", 0) < cutoff:
                resident.handled.mark(m["id"])
                continue
            found[m["id"]] = m
    out = [found[k] for k in sorted(found)]
    if out:
        logger.info(f"Backlog: {len(out)} message(s) queued behind registration")
    return out


def run(resident):
    backfill(resident)
    reg = resident.chat.register(event_types=EVENTS)
    if reg.get("result") != "success":
        logger.error(f"Could not register an event queue: {reg}")
        sys.exit(1)
    queue_id, last_id = reg["queue_id"], reg["last_event_id"]
    pending = deque(backlog(resident))
    replayed = {m["id"] for m in pending}
    logger.info(f"Listening as {resident.name}")

    def fetch(block: bool):
        nonlocal queue_id, last_id
        r = resident.chat.get_events(queue_id=queue_id, last_event_id=last_id,
                                     dont_block=not block)
        if r.get("result") != "success":
            if r.get("code") == "BAD_EVENT_QUEUE_ID":
                reg = resident.chat.register(event_types=EVENTS)
                queue_id, last_id = reg["queue_id"], reg["last_event_id"]
                logger.warning("Event queue expired; re-registered")
            elif block:
                time.sleep(5)
            return
        for ev in r.get("events", []):
            last_id = max(last_id, ev["id"])
            if ev["type"] == "message" and ev["message"]["id"] not in replayed:
                pending.append(ev["message"])
            elif ev["type"] == "typing":
                resident.on_typing(ev)

    def take(room) -> list[dict]:
        hits = [m for m in pending if room_of(m) == room]
        for m in hits:
            pending.remove(m)
        return hits

    def sweep(room):
        fetch(block=False)
        return take(room)

    while True:
        try:
            if not pending:
                fetch(block=True)
            if pending:
                msg = pending.popleft()
                room = room_of(msg)
                resident.handle_message(msg, take(room), sweep=lambda r=room: sweep(r))
            if resident.restart_requested:
                logger.info("Restart requested (self-edit) — exiting for the supervisor")
                sys.exit(42)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            logger.error(f"loop: {e}", exc_info=True)
            time.sleep(2)
