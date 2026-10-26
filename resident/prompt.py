"""The system prompt: how the harness works, who the resident is, what it
remembers, and — on a cold start — what has been happening in the room.

Clocks never go in here: the system prompt is the cached prefix, and a
timestamp in it would invalidate the cache every turn. The time travels in
each turn's own header instead (resident.py).
"""

from resident import profile
from resident.util import room_file, safe_filename, tail
from resident.config import STREAM_ACTIVITY_CHARS, TOPIC_HISTORY_CHARS

HOW_IT_WORKS = """## How a turn works

Your plain text is private; the room hears you only through send_message,
send_dm and add_reaction. One reply is one send_message. Staying quiet is
fine: end the turn without posting, and if the harness asks whether your
text was meant for the room, answer noop.

A judge decides which ordinary messages in your rooms start a turn; a DM,
being named, or your move in a game always does. Messages that arrive
together come as one turn. <said_since_your_last_turn> is what was said in
the room since your last turn there that the judge didn't wake you for:
your first look at it. Where several residents share a room, one composes
at a time; if another has the floor, your turn ends without posting,
unless you were named. Each turn opens with [now: …], your clock.

Rooms run short: a sentence or two is a normal reply. Give depth when it is
asked for. A very long message is held as a draft for you to trim or send
as is.

## Memory

Loaded every turn: scratchpad.md, the end of journal.md, and any files your
profile names. On a cold start, also: the room's recent history,
<room_memory> (its older history, distilled), your note on this room's last
conversation, and notes on the people talking. Everything else (people/,
every room's log under channels/, your past turns in api_archive.jsonl) is
a tool call away. What you don't write down doesn't outlast the
conversation.
"""


class Prompts:
    def __init__(self, state):
        self.state = state

    def system(self, stream: str, topic: str, cold: bool, people=()) -> str:
        p = profile.current()
        parts = [HOW_IT_WORKS, p.identity.strip()]
        for name in p.prompt.files:
            text = (self.state.read_file(name) or "").strip()
            if text:
                parts.append(f'<file name="{name}">\n{tail(text, p.prompt.file_chars, name)}\n</file>')
        scratch = (self.state.read_file("scratchpad.md") or "").strip()
        if scratch:
            scratch = tail(scratch, p.prompt.scratchpad_chars, "scratchpad.md")
            parts.append(f"<scratchpad>\n{scratch}\n</scratchpad>")
        journal = self.state.read_file("journal.md") or ""
        if len(journal.strip()) > len("# Journal"):
            parts.append(f"<journal_tail>\n{tail(journal.strip(), p.prompt.journal_chars, 'journal.md')}"
                         "\n</journal_tail>")
        if cold:
            from resident.memory.distill import room_memory
            memory = room_memory(self.state, stream, topic)
            if memory:
                parts.append(f'<room_memory stream="{stream}" topic="{topic}">\n'
                             "What you keep of this room's older history:\n"
                             f"{memory}\n</room_memory>")
            history = self.state.topic_history(stream, topic, TOPIC_HISTORY_CHARS)
            if history:
                parts.append(f'<room_history stream="{stream}" topic="{topic}">\n'
                             f"{history}\n</room_history>")
            nearby = self.state.stream_activity(stream, STREAM_ACTIVITY_CHARS, exclude_topic=topic)
            if nearby:
                parts.append(f'<elsewhere_in stream="{stream}">\n{nearby}\n</elsewhere_in>')
            if p.prompt.people:
                for who in sorted(x for x in people if x and x.lower() != p.bot_name):
                    note = self.state.read_file(f"people/{safe_filename(who)}.md")
                    if note:
                        parts.append(f'<person_notes name="{who}">\n'
                                     f"{note[-2000:].strip()}\n</person_notes>")
            notes = self.state.read_file(f"sessions/{room_file(stream, topic)}.md")
            if notes:
                parts.append(f"<your_notes_on_this_room>\n{notes[-6000:].strip()}\n"
                             "</your_notes_on_this_room>")
        return "\n\n".join(x for x in parts if x)

    def birth(self, stream: str, topic: str) -> str:
        """The first turn of a persistent room carries its history itself."""
        history = self.state.topic_history(stream, topic,
                                           profile.current().limits.birth_raw_chars)
        return ("<room_history_for_a_long_conversation>\nThis room is one long "
                "conversation you keep across days. Here is what has been said "
                f"so far:\n{history or '(nothing yet)'}\n"
                "</room_history_for_a_long_conversation>")

