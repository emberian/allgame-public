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
        # The harness names streams; Zulip's typing endpoint wants the id
        # (a name is refused, and the error was easy to miss).
        if payload.get("type") == "stream" and isinstance(payload.get("to"), str):
            sid = self._stream_ids().get(payload["to"].lower())
            if sid is None:
                return {"result": "error", "msg": f"unknown stream {payload['to']}"}
            payload = {"op": payload["op"], "type": "stream", "stream_id": sid,
                       "topic": payload.get("topic", "")}
        return self.client.set_typing_status(payload)

    def _stream_ids(self) -> dict:
        """{lowercased name: id}, fetched once and refreshed on a miss."""
        if not getattr(self, "_ids", None):
            r = self.client.get_streams()
            streams = r.get("streams", [])
            self._ids = {st["name"].lower(): st["stream_id"] for st in streams}
            self._names = {st["stream_id"]: st["name"] for st in streams}
        return self._ids

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
        r = self.client.get_events(queue_id=queue_id, last_event_id=last_event_id,
                                   dont_block=dont_block)
        # Typing events carry a stream id; the harness keys rooms by name.
        names = None
        for ev in r.get("events", []) or []:
            if ev.get("type") == "typing" and ev.get("stream_id") is not None \
                    and not ev.get("stream_name"):
                if names is None:
                    self._stream_ids()
                    names = self._names
                ev["stream_name"] = names.get(ev["stream_id"], str(ev["stream_id"]))
        return r
