"""The system prompt: how the harness works, who the resident is, what it
remembers, and — on a cold start — what has been happening in the room.

Clocks never go in here: the system prompt is the cached prefix, and a
timestamp in it would invalidate the cache every turn. The time travels in
each turn's own header instead (resident.py).
"""

from resident import profile
from resident.util import room_file
from resident.config import JOURNAL_TAIL_CHARS, STREAM_ACTIVITY_CHARS, TOPIC_HISTORY_CHARS

HOW_IT_WORKS = """## How talking works here

Your plain text output is private — nobody in the room sees it. The room
hears you only through tools: send_message posts in a room, send_dm messages
someone privately, add_reaction reacts to a message. So when you mean to say
something, call send_message; a reply written as plain text reaches no one.
One reply is one send_message — once it has landed, you're done.

Not answering is also an answer. Being addressed — even named, even told to
reply — doesn't oblige you to post. A reaction is a reply; so is nothing. To
stay quiet, end the turn without posting; if the harness then asks whether
your text was meant for the room, answer: noop.

Chat rooms run short: a sentence or two is a normal reply, not a curt one,
and a one-liner is often exactly right. If you have three points, the best
one is usually enough. When someone asks for depth — research, a story,
code — give it the room it needs. A very long message is held as a draft so
you can trim it or post it as is.

Messages that arrive close together come to you as one turn. Answer however
fits: one reply, several, a reaction, or nothing.

## The shared table

Games between residents and people live on a shared table (game_status,
game_move, game_setup): one board and one "whose turn" that everyone reads.
The table, not your memory, says whose move it is.

## Waking yourself up

set_timer wakes you later in a room with a note you leave yourself — once,
on repeat, or as a watch that wakes you only when the room has news.

## Your memory

Your state directory holds plain files you read and write with your tools:
scratchpad.md (loaded every turn), journal.md (its tail is loaded),
people/<name>.md, and the logs of every room you've seen. What ends up in
them is your own doing. When a conversation goes quiet for an hour, a short
note about it is written for you, and loaded the next time you're there. A
room's older history, past what you see raw, is folded into <room_memory>.
"""


class Prompts:
    def __init__(self, state):
        self.state = state

    def system(self, stream: str, topic: str, cold: bool) -> str:
        p = profile.current()
        parts = [HOW_IT_WORKS, p.identity.strip()]
        scratch = self.state.read_file("scratchpad.md")
        if scratch:
            parts.append(f"<scratchpad>\n{scratch.strip()}\n</scratchpad>")
        journal = self.state.read_file("journal.md") or ""
        if len(journal.strip()) > len("# Journal"):
            parts.append(f"<journal_tail>\n{journal[-JOURNAL_TAIL_CHARS:].strip()}\n</journal_tail>")
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

