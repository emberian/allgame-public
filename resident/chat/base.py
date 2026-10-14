"""The chat platform a resident lives on, behind one seam.

Everything after the event loop — judge, prompt, engine, tools, memory — talks
to the platform only through this protocol. Method names and payloads follow
the `zulip.Client` API, because Zulip's model (streams, topics, an event
queue) is the shape the rest of the harness is written against; another
platform implements the same calls and translates.

## The normalized message

What a backend's events and history reads must produce:

    id                  int    message id, unique within the platform
    type                str    "stream" for a channel, "private" for a DM
    content             str    the message text
    sender_full_name    str    display name
    sender_email        str    a STABLE unique id for the sender — notes about
                               people and DM routing key off it
    display_recipient   str    channel name (or the DM recipient list)
    subject             str    topic; a platform without topics uses the
                               channel name
    timestamp           int    unix seconds

A room is always addressed as (stream, topic).
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class ChatBackend(Protocol):
    name: str
    # Hard per-message ceiling of the platform. The sender splits to it.
    max_message_chars: int

    def get_profile(self) -> dict:
        """{"full_name": ...} — who the resident is here."""

    def send_message(self, payload: dict) -> dict:
        """{"type": "stream", "to": stream, "topic": topic, "content": text}
        or {"type": "private", "to": [user ids], "content": text}.
        Returns {"result": "success"|"error", "id": ...}."""

    def add_reaction(self, payload: dict) -> dict:
        """{"message_id", "emoji_name"}"""

    def set_typing_status(self, payload: dict) -> dict:
        """Best-effort typing indicator; never load-bearing."""

    def get_members(self) -> dict:
        """{"members": [{"user_id", "full_name", "email", ...}]}"""

    def get_messages(self, request: dict) -> dict:
        """History. Request keys used: anchor ("newest"), num_before,
        num_after, narrow ([{operator, operand}] with operators `channel`,
        `topic`, `is`="private"), apply_markdown. Returns
        {"result", "messages": [normalized...]}."""

    def register(self, event_types: list[str], narrow: list | None = None) -> dict:
        """Open an event queue: {"result", "queue_id", "last_event_id"}."""

    def get_events(self, queue_id: str, last_event_id: int,
                   dont_block: bool = False) -> dict:
        """{"result", "events": [{"id", "type", ...}]}. A dead queue answers
        {"result": "error", "code": "BAD_EVENT_QUEUE_ID"}."""
