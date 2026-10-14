"""Should the resident pay attention to this message?

Two stages, cheapest first:

1. CERTAINTIES, free: a DM, someone saying the resident's name, its move on
   the shared game table. These are facts, decided by a string comparison
   or a file read, and a message carrying one never waits behind a model
   call — a busy room otherwise makes "@name hello?" queue behind several
   expensive decisions to ignore chatter.

2. THE JUDGE, a small model, for everything ambiguous in the resident's
   rooms. It is a COST GATE, not the final word: YES means "worth a turn",
   and in that turn the resident still decides whether to reply, react, or
   stay quiet.

Lurk decay: the longer nobody addresses the resident in a room, the higher
the bar, so it drifts toward listening until someone draws it back in.

The judge may leave a short remark. Remarks on messages it passed ride
along with the turn; remarks on messages it declined queue up and arrive
with the resident's next turn in that room — its peripheral vision for what
it never saw.

Failure is OPEN: if the judge can't be reached, engage. Being briefly too
talkative beats silently ignoring people.
"""

import json
import logging
import re
import threading
import time

from resident import profile

logger = logging.getLogger("judge")

TIERS = ((3, "active"), (8, "drifting"), (15, "lurking"), (10**9, "silent"))

STANCE = {
    "active": ("{name} is actively part of this conversation. Lean YES — it "
               "can decide for itself whether to reply, react, or just read.",
               "- {name} is mentioned, referenced, or asked something\n"
               "- the topic is one {name} has been part of\n"
               "- {name} might want to react, even with an emoji\n"
               "- someone shared something interesting"),
    "drifting": ("{name} hasn't been addressed in {count} messages. Still "
                 "worth attention in topics it's been part of.",
                 "- {name} is mentioned, referenced, or asked something\n"
                 "- the topic is one {name} has been part of\n"
                 "- {name} has a perspective or reaction to add"),
    "lurking": ("{name} hasn't been addressed in {count} messages. Only if "
                "clearly relevant to it.",
                "- {name} is mentioned or asked something\n"
                "- the topic is something {name} cares about\n"
                "- {name} has a real reaction or unique insight"),
    "silent": ("{name} hasn't been addressed in {count} messages. Only if "
               "called on or the topic is about it.",
               "- {name} is named\n- the conversation is about {name}\n"
               "- a question only {name} can answer"),
}


def parse_verdict(raw: str) -> dict:
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"no verdict in judge output: {raw[:120]!r}")
    data = json.loads(text[start:end + 1])
    data["decision"] = str(data.get("decision", "")).upper()
    if data["decision"] not in ("YES", "NO"):
        raise ValueError(f"verdict missing decision: {data}")
    return data


class Judge:
    def __init__(self, state):
        p = profile.current()
        self.state = state
        self.name = p.name
        self.bot_name = p.bot_name
        self.rooms = [s.lower() for s in p.chat.rooms]
        self.mention_only = [s.lower() for s in p.chat.mention_only]
        self.peers = p.chat.peers
        self._since: dict[str, int] = {}          # messages since addressed
        self._remarks: dict[tuple, list] = {}
        self._lock = threading.Lock()
        self.last_reason = ""
        self.last_remark = ""
        self.errored = False

    # ---- lurk decay -------------------------------------------------------

    def record(self, stream: str, direct: bool):
        k = stream.lower()
        self._since[k] = 0 if direct else self._since.get(k, 0) + 1

    def unrecord(self, stream: str):
        """A turn that ended without speaking shouldn't count against it."""
        k = stream.lower()
        self._since[k] = max(0, self._since.get(k, 0) - 1)

    def lurk(self, stream: str) -> tuple[str, int]:
        n = self._since.get(stream.lower(), 0)
        return next(label for limit, label in TIERS if n <= limit), n

    # ---- stage 1: certainties ---------------------------------------------

    def named(self, content: str) -> bool:
        c = (content or "").lower()
        return f"@**{self.bot_name}**" in c or f"@{self.bot_name}" in c

    def certain(self, message: dict, stream: str) -> str | None:
        """A reason to engage that costs nothing to determine, or None."""
        if self.bot_name in (message.get("sender_full_name") or "").lower():
            return None
        if message.get("type") == "private":
            return "DM"
        s = stream.lower()
        if s not in self.rooms and s not in self.mention_only:
            return None
        if self.named(message.get("content", "")):
            return "named"
        if s in self.mention_only:
            return None
        try:
            from resident.tools import games
            game = games.my_turn(stream, message.get("subject", ""), self.name)
        except Exception:
            game = None
        if game:
            return f"my move in {game['game']} (turn {game.get('turn')})"
        return None

    # ---- stage 2: the model -------------------------------------------------

    def worth_a_turn(self, message: dict, stream: str) -> bool:
        self.errored = False
        self.last_remark = ""
        s = stream.lower()
        if s in self.mention_only or s not in self.rooms:
            self.last_reason = "not one of my rooms" if s not in self.rooms else "mention-only room"
            return False
        self.record(stream, direct=False)
        topic = message.get("subject", "")
        sender = message.get("sender_full_name", "someone")
        lurk, count = self.lurk(stream)
        stance, yes = (t.format(name=self.name, count=count) for t in STANCE[lurk])
        peers = (f"- the message is from another AI resident ({', '.join(self.peers)}) "
                 f"and no human is steering: two bots riffing can ping-pong a room "
                 f"forever. YES only if it asks {self.name} something specific or "
                 f"advances a game or task already under way\n") if self.peers else ""
        prompt = f"""You decide whether {self.name}, an AI member of a group chat, \
should give the latest message a turn. A turn means {self.name} sees it and \
decides what to do — reply, react, take notes, or just read. This is a cost \
gate, not the final word; when in doubt, YES.

{stance}

Room: #{stream} > {topic}

Recent conversation:
{self.state.topic_history(stream, topic, 3000) or "(none)"}

LATEST, from {sender}:
{message.get('content', '')}

YES if any apply:
{yes}

NO if any apply:
- it's between other people and {self.name} has nothing specific to add
- answering would interrupt people talking to each other
- it's logistics, or casual chatter that needs no input
- {self.name} would only agree or restate what was said
{peers}- a message arriving right after {self.name} spoke is usually a REPLY to \
it: "it just spoke" is a reason to be brief, never by itself a reason to leave \
someone hanging

You may leave a short remark for {self.name} — a heads-up, a mood you \
noticed, a pattern across recent messages. Remarks on a NO reach it with its \
next turn in this room, so you are its peripheral vision. Most messages need \
none.

Respond with ONLY a JSON object: {{"decision": "YES"|"NO", "reason": \
"<few words>", "remark": "<optional>"}}"""
        try:
            from resident import llm
            v = parse_verdict(llm.complete(prompt, model=profile.current().models.judge))
        except Exception as e:
            self.errored = True
            self.last_reason = f"judge unavailable, engaging anyway ({e})"
            logger.warning(f"Judge call failed: {e}")
            return True
        remark = str(v.get("remark") or "").strip()
        self.last_reason = f"[{lurk}/{count}] {v['decision']} — {v.get('reason', '')}"
        logger.info(f"Judge [{sender} in #{stream}>{topic}] {self.last_reason}")
        if v["decision"] == "YES":
            self.last_remark = remark
            return True
        if remark:
            with self._lock:
                self._remarks.setdefault((stream, topic), []).append(
                    {"ts": time.time(), "about": sender, "remark": remark})
        return False

    def drain_remarks(self, stream: str, topic: str) -> list[dict]:
        with self._lock:
            return self._remarks.pop((stream, topic), [])
