"""Zulip: the native shape of the harness, so this is nearly a passthrough."""

from pathlib import Path


class ZulipBackend:
    name = "zulip"
    max_message_chars = 9_000     # Zulip's own limit is ~10k

    def __init__(self, zuliprc: str):
        import zulip
        self.client = zulip.Client(config_file=str(Path(zuliprc).expanduser()))

    def get_profile(self) -> dict:
        return self.client.get_profile()

    def send_message(self, payload: dict) -> dict:
        return self.client.send_message(payload)

    def add_reaction(self, payload: dict) -> dict:
        return self.client.add_reaction(payload)

    def set_typing_status(self, payload: dict) -> dict:
        return self.client.set_typing_status(payload)

    def get_members(self) -> dict:
        return self.client.get_members()

    def get_messages(self, request: dict) -> dict:
        import json
        req = dict(request)
        if isinstance(req.get("narrow"), list):
            req["narrow"] = json.dumps(req["narrow"])
        return self.client.get_messages(req)

    def register(self, event_types, narrow=None) -> dict:
        return self.client.register(event_types=event_types, narrow=narrow or [])

    def get_events(self, queue_id, last_event_id, dont_block=False) -> dict:
        return self.client.get_events(queue_id=queue_id,
                                      last_event_id=last_event_id,
                                      dont_block=dont_block)
