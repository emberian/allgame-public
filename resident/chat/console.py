"""An in-memory chat: one process, no server. For trying a resident locally
and for tests.

`MemoryChat` keeps every message and serves them through the same event
queue API as Zulip. `run_console` attaches it to the terminal: lines you type
are posted as a human in one room, and the resident's posts are printed.

    you> @juniper what are you reading?
    juniper> a book about tide clocks
"""

import itertools
import queue
import threading
import time


class MemoryChat:
    name = "console"
    max_message_chars = 9_000

    def __init__(self, resident_name: str):
        self.resident_name = resident_name
        self._ids = itertools.count(1)
        self._event_ids = itertools.count(0)
        self.messages: list[dict] = []
        self.reactions: list[dict] = []
        self._events: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self.on_post = None        # callback(message) for resident posts

    # ---- what a human (or a test) does ---------------------------------

    def say(self, sender: str, content: str, stream: str = "lounge",
            topic: str = "general", private: bool = False) -> dict:
        msg = self._store(sender, f"{sender.lower()}@local", content,
                          stream, topic, private)
        self._events.put({"type": "message", "message": msg})
        return msg

    # ---- the backend protocol -------------------------------------------

    def get_profile(self) -> dict:
        return {"result": "success", "full_name": self.resident_name,
                "email": f"{self.resident_name.lower()}@local", "user_id": 0}

    def send_message(self, payload: dict) -> dict:
        private = payload.get("type") == "private"
        msg = self._store(self.resident_name, f"{self.resident_name.lower()}@local",
                          payload["content"], payload.get("to", ""),
                          payload.get("topic", ""), private)
        # Echo it as an event, as Zulip does: the loop logs every message it
        # sees, the resident's own included, from one place.
        self._events.put({"type": "message", "message": msg})
        if self.on_post:
            self.on_post(msg)
        return {"result": "success", "id": msg["id"]}

    def add_reaction(self, payload: dict) -> dict:
        self.reactions.append(dict(payload))
        if self.on_post:
            self.on_post({"reaction": payload})
        return {"result": "success"}

    def set_typing_status(self, payload: dict) -> dict:
        return {"result": "success"}

    def get_members(self) -> dict:
        names = {m["sender_full_name"]: m["sender_email"] for m in self.messages}
        return {"result": "success", "members": [
            {"user_id": i + 1, "full_name": n, "email": e, "is_active": True}
            for i, (n, e) in enumerate(names.items())]}

    def get_messages(self, request: dict) -> dict:
        narrow = request.get("narrow") or []
        out = self.messages
        for n in narrow:
            op, val = n.get("operator"), n.get("operand")
            if op in ("channel", "stream"):
                out = [m for m in out if m["type"] == "stream"
                       and m["display_recipient"] == val]
            elif op == "topic":
                out = [m for m in out if m["subject"] == val]
            elif op == "is" and val == "private":
                out = [m for m in out if m["type"] == "private"]
        n = int(request.get("num_before", 50))
        return {"result": "success", "messages": out[-n:] if n else []}

    def register(self, event_types, narrow=None) -> dict:
        return {"result": "success", "queue_id": "memory", "last_event_id": -1}

    def get_events(self, queue_id, last_event_id, dont_block=False) -> dict:
        events = []
        try:
            ev = self._events.get(block=not dont_block, timeout=None if not dont_block else 0)
            events.append(ev)
            while True:
                events.append(self._events.get_nowait())
        except queue.Empty:
            pass
        for ev in events:
            ev["id"] = next(self._event_ids)
        return {"result": "success", "events": events}

    # ---- internals --------------------------------------------------------

    def _store(self, sender, email, content, stream, topic, private) -> dict:
        with self._lock:
            msg = {"id": next(self._ids), "type": "private" if private else "stream",
                   "content": content, "sender_full_name": sender,
                   "sender_email": email,
                   "display_recipient": stream if not private else [{"email": email}],
                   "subject": topic if not private else "", "timestamp": int(time.time())}
            self.messages.append(msg)
        return msg


def run_console(chat: MemoryChat, stream: str, topic: str = "general",
                human: str = "you"):
    """Read lines from the terminal as `human`; print the resident's posts."""
    def show(m):
        if "reaction" in m:
            print(f"  [{chat.resident_name} reacted :{m['reaction'].get('emoji_name')}:]")
        else:
            print(f"{chat.resident_name.lower()}> {m['content']}")
    chat.on_post = show
    print(f"#{stream} > {topic} — talking to {chat.resident_name}. Ctrl-D to leave.")
    try:
        while True:
            line = input(f"{human}> ").strip()
            if line:
                chat.say(human, line, stream=stream, topic=topic)
    except (EOFError, KeyboardInterrupt):
        print()
