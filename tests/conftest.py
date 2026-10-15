"""Shared fixtures. No test here makes a model call or touches ~/.resident:
the shared directory is a temp dir, the model is a stub, and the engine is
replaced by a fake that posts through the in-memory chat."""

import os
import tempfile

# Before anything imports resident.floor, which reads it at import time.
os.environ["RESIDENT_SHARED_DIR"] = tempfile.mkdtemp(prefix="resident_shared_")

import pytest  # noqa: E402

from resident import profile  # noqa: E402

PROFILE = """
[resident]
name = "Juniper"
state_dir = "{state}"
[chat]
rooms = ["lounge", "quiet"]
mention_only = ["quiet"]
peers = ["Rowan"]
[wake]
solitude = false
[limits]
message_soft_limit = 200
"""


@pytest.fixture
def prof(tmp_path):
    d = tmp_path / "profile"
    d.mkdir()
    (d / "profile.toml").write_text(PROFILE.format(state=tmp_path / "state"))
    (d / "identity.md").write_text("You are Juniper.\n")
    return profile.load(d)


@pytest.fixture
def state(prof):
    from resident.memory.state import StateManager
    return StateManager(prof.state_dir)


@pytest.fixture
def chat(prof):
    from resident.chat.console import MemoryChat
    return MemoryChat(prof.name)


class FakeEngine:
    """Stands in for the Agent SDK: records each turn's prompt and, unless
    told to stay quiet, posts one reply through the send_message tool."""

    def __init__(self, resident, reply="hello!"):
        self.r, self.reply, self.prompts = resident, reply, []
        self.last_progress = 0.0     # the typing pulse reads this heartbeat

    def respond(self, session, stream, topic, sender, trigger, prompt_text, quiet=False):
        from resident.tools import registry
        self.prompts.append(prompt_text)
        session.transcript.append({"role": "resident", "text": self.reply or ""})
        if not self.reply:
            return "", []
        ctx = registry.Context(state=self.r.state, chat=self.r.chat, stream=stream,
                               topic=topic, sender=sender, message=trigger or {})
        out = registry.run("send_message", {"content": self.reply}, ctx)
        assert not out.get("is_error"), out
        return self.reply, list(ctx.posted_ids)


@pytest.fixture
def resident(prof, state, chat, monkeypatch):
    import resident.resident as rmod
    monkeypatch.setattr(rmod, "COOLDOWN_SECONDS", 0)
    r = rmod.Resident(chat, state)
    r.engine = FakeEngine(r)
    return r
