"""One resident: from a batch of messages to (maybe) a turn.

    handle_message(batch)
      1. log every message; drop ones already handled, and the resident's own
      2. find a trigger: a free certainty anywhere in the batch (DM, named,
         its move), else the judge, message by message
      3. take the floor — or, if named, go ahead even without it
      4. show typing; wait out anyone else typing; sweep up stragglers
      5. one turn through the engine, with every message in the batch
      6. mark the batch handled (a crashed turn is left for a retry)

Self-started turns (timers, solitude, the game clock) go through run_turn.
"""

import logging
import threading
import time

from resident import floor, profile
from resident.config import (BURST_SWEEP_INTERVAL, BURST_SWEEP_ROUNDS, COOLDOWN_SECONDS,
                             INTERIM_MESSAGES_CHARS, MAX_TOOL_TURNS, TYPING_HOLDOFF_TIMEOUT)
from resident.engine import Engine
from resident.judge import Judge
from resident.memory.handled import HandledSet
from resident.memory.sessions import Sessions
from resident.prompt import Prompts
from resident.util import fmt_log_time, fmt_now, human_gap

logger = logging.getLogger("resident")

TYPING_PULSE = 8
TYPING_STALL = 300


def room_of(message: dict) -> tuple[str, str]:
    if message.get("type") == "private":
        return "dm", message.get("sender_email", "unknown")
    return message.get("display_recipient", "unknown"), message.get("subject", "")


class Resident:
    def __init__(self, chat, state):
        self.chat = chat
        self.state = state
        self.name = profile.current().name
        self.bot_name = profile.current().bot_name
        self.judge = Judge(state)
        self.sessions = Sessions()
        self.handled = HandledSet(state)
        self.prompts = Prompts(state)
        self.max_tool_turns = MAX_TOOL_TURNS
        self.engine = Engine(self)
        self.restart_requested = False
        self._typing: dict[tuple, float] = {}       # (stream, topic) -> last seen
        self._seen_ids: set[int] = set()

    # ---- events from the loop -------------------------------------------------

    def on_typing(self, event: dict):
        if event.get("op") == "start" and event.get("stream_id") is not None:
            key = (event.get("stream_name") or str(event["stream_id"]), event.get("topic", ""))
            self._typing[key] = time.time()

    # ---- a batch ----------------------------------------------------------------

    def _prepare(self, m: dict, stream: str, topic: str) -> bool:
        mid = m.get("id")
        if mid in self.handled:
            return False
        if mid not in self._seen_ids:
            self.state.log_message(stream, topic, m.get("sender_full_name", "?"),
                                   m.get("content", ""), m.get("timestamp"), mid)
            self._seen_ids.add(mid)
        if self.bot_name in (m.get("sender_full_name") or "").lower():
            self.handled.mark(mid)
            return False
        return True

    def handle_message(self, message: dict, followups=(), sweep=None):
        stream, topic = room_of(message)
        batch = [m for m in [message, *followups] if self._prepare(m, stream, topic)]
        if not batch:
            return

        trigger = None
        for i, m in enumerate(batch):
            reason = self.judge.certain(m, stream)
            if reason:
                trigger, self.judge.last_reason = m, reason
                self.judge.last_remark = ""
                self.judge.record(stream, direct=True)
                break
        if trigger is None:
            for m in batch:
                if self.judge.worth_a_turn(m, stream):
                    trigger = m
                    break
                if not self.judge.errored:
                    self.handled.mark(m.get("id"))
        if trigger is None:
            return

        time.sleep(COOLDOWN_SECONDS)
        named = bool(self.judge.certain(trigger, stream))
        got, holder = floor.acquire(stream, topic, self.name,
                                    trigger_id=trigger.get("id"), addressed=named)
        if not got and not named:
            logger.info(f"Floor in #{stream}>{topic} held by "
                        f"{(holder or {}).get('who')} — standing down")
            for m in batch:
                self.handled.mark(m.get("id"))
            return
        stop = self._typing_pulse(stream, topic)
        try:
            self._wait_for_typing(stream, topic)
            for _ in range(BURST_SWEEP_ROUNDS if sweep else 0):
                new = [m for m in (sweep() or []) if self._prepare(m, stream, topic)]
                if not new:
                    break
                batch += new
                time.sleep(BURST_SWEEP_INTERVAL)
            logger.info(f"Responding to {trigger.get('sender_full_name')} in "
                        f"#{stream}>{topic} ({len(batch)} msg) [{self.judge.last_reason}]")
            self._turn(stream, topic, batch, trigger)
        finally:
            stop.set()
            floor.release(stream, topic, self.name)

    def _turn(self, stream, topic, batch, trigger):
        session, cold = self.sessions.get_or_create(stream, topic)
        session.touch()
        ids = [m["id"] for m in batch if m.get("id")]
        blocks = []
        if not cold and session.last_seen_msg_id:
            interim = self.state.messages_since(stream, topic, session.last_seen_msg_id,
                                                set(ids), self.bot_name, INTERIM_MESSAGES_CHARS)
            if interim:
                blocks.append("<said_since_your_last_turn>\n(you chose not to answer "
                              f"these; context only)\n{interim}\n</said_since_your_last_turn>")
        now = trigger.get("timestamp") or time.time()
        header = f"[now: {fmt_now(now)}"
        prev = session.last_msg_ts or self.state.previous_message_time(stream, topic, set(ids))
        if prev and now - prev >= 90:
            header += f" · {human_gap(now - prev)} since the last message you saw here"
        blocks.append(header + "]")
        if len(batch) > 1:
            blocks.append(f"[{len(batch)} messages arrived together — answer however fits.]")
        for m in batch:
            blocks.append(f"[{fmt_log_time(m.get('timestamp') or now)}] "
                          f"{m.get('sender_full_name')}: {m.get('content', '')}")
            session.participants.add(m.get("sender_full_name"))
        notes = self.judge.drain_remarks(stream, topic)
        if notes:
            blocks.append("<judge_notes>\nYour judge, on messages it handled while "
                          "you weren't looking:\n" + "\n".join(
                              f"  · re {n['about']}: {n['remark']}" for n in notes)
                          + "\n</judge_notes>")
        if self.judge.last_remark:
            blocks.append(f"<judge_remark>{self.judge.last_remark}</judge_remark>")
        session.last_seen_msg_id = max([session.last_seen_msg_id, *ids])
        session.last_msg_ts = max(session.last_msg_ts,
                                  *[m.get("timestamp") or 0 for m in batch])
        session.transcript.append({"role": "room", "text": "\n".join(blocks)})
        try:
            said, posted = self.engine.respond(session, stream, topic,
                                               trigger.get("sender_full_name", ""),
                                               trigger, "\n\n".join(blocks))
        except Exception as e:
            logger.error(f"Turn failed in #{stream}>{topic}: {e}", exc_info=True)
            self.sessions.end(stream, topic)
            return
        for mid in ids:
            self.handled.mark(mid)
        if posted:
            self.judge.record(stream, direct=True)
            logger.info(f"Posted {len(posted)} message(s) in #{stream}>{topic}")
        else:
            self.judge.unrecord(stream)
            logger.info(f"Stayed quiet in #{stream}>{topic}")

    # ---- self-started turns --------------------------------------------------

    def run_turn(self, stream: str, topic: str, prompt: str):
        """A turn nobody asked for: a timer, a quiet moment, a stalled game."""
        session, _ = self.sessions.get_or_create(stream, topic)
        session.touch()
        self.engine.respond(session, stream, topic, "", {"type": "stream",
                            "display_recipient": stream, "subject": topic},
                            f"[now: {fmt_now()}]\n\n{prompt}", quiet=True)

    # ---- typing -----------------------------------------------------------------

    def _typing_pulse(self, stream, topic) -> threading.Event:
        """Show typing while composing, renewing the floor with each pulse.

        It stops on SILENCE, not on the clock: a turn that is still producing
        model output or running tools is working, however long it takes, and
        the indicator says so. Only TYPING_STALL seconds with no sign of life
        (a stuck turn) makes it give up early."""
        stop = threading.Event()
        started = time.time()

        def pulse():
            while not stop.is_set():
                if time.time() - max(started, self.engine.last_progress) > TYPING_STALL:
                    logger.warning(f"Typing in #{stream}>{topic}: no progress for "
                                   f"{TYPING_STALL}s — stopping")
                    break
                self.chat.set_typing_status({"op": "start", "type": "stream",
                                             "to": stream, "topic": topic})
                # A turn taken without the floor (because it was named) has
                # nothing to renew; it keeps typing all the same.
                floor.renew(stream, topic, self.name)
                stop.wait(TYPING_PULSE)
            self.chat.set_typing_status({"op": "stop", "type": "stream",
                                         "to": stream, "topic": topic})
        if stream != "dm":
            threading.Thread(target=pulse, daemon=True).start()
        return stop

    def _wait_for_typing(self, stream, topic):
        start = time.time()
        while time.time() - start < TYPING_HOLDOFF_TIMEOUT:
            if time.time() - self._typing.get((stream, topic), 0) > 6:
                return
            time.sleep(1)
