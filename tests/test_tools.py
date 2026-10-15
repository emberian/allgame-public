import time

import pytest

from resident import profile
from resident.tools import registry


@pytest.fixture
def ctx(state, chat, prof):
    return registry.Context(state=state, chat=chat, stream="lounge", topic="general",
                            message={"type": "stream"})


def test_every_tool_has_a_sound_schema(prof):
    tools = registry.enabled(profile.current().tools)
    assert {t.name for t in tools} >= {"send_message", "read_state_file", "set_timer",
                                       "game_move", "search_messages", "fetch_url"}
    for t in tools:
        assert t.description, t.name
        assert set(t.schema["required"]) <= set(t.schema["properties"]), t.name


def test_long_message_is_held_then_sent_as_is(ctx, chat):
    long = "word " * 100                                   # over the 200-char limit
    out = registry.run("send_message", {"content": long}, ctx)
    assert "Held as a draft" in out["content"] and not chat.messages
    out = registry.run("send_message", {"send_as_is": True}, ctx)
    assert not out.get("is_error"), out
    assert chat.messages[-1]["content"] == long.strip()


def test_state_files_stay_inside_the_state_dir(ctx):
    registry.run("write_state_file", {"path": "notes/a.md", "content": "hi"}, ctx)
    assert registry.run("read_state_file", {"path": "notes/a.md"}, ctx)["content"].endswith("hi")
    out = registry.run("read_state_file", {"path": "../../etc/passwd"}, ctx)
    assert out.get("is_error")


def test_timer_fires_once(ctx, state, monkeypatch):
    from resident.wake import timers
    out = registry.run("set_timer", {"note": "check back", "in_minutes": 1}, ctx)
    assert not out.get("is_error"), out
    assert timers.due_now(state) == []
    later = time.time() + 120
    monkeypatch.setattr(timers.time, "time", lambda: later)
    fired = timers.due_now(state)
    assert len(fired) == 1 and "check back" in fired[0][1]
    assert timers.due_now(state) == []                    # claimed, not refired


def test_timer_rejects_nonsense(ctx):
    assert registry.run("set_timer", {"note": "x"}, ctx).get("is_error")
    assert registry.run("set_timer", {"note": "x", "every_minutes": 0}, ctx).get("is_error")
