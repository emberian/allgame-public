"""A message in, through judge and floor, to a (fake) turn and a post."""

import json


def test_mention_gets_a_reply(resident, chat):
    m = chat.say("ember", "@**Juniper** hi!", "lounge", "general")
    resident.handle_message(m)
    posted = [x for x in chat.messages if x["sender_full_name"] == "Juniper"]
    assert [p["content"] for p in posted] == ["hello!"]
    assert m["id"] in resident.handled
    assert "ember: @**Juniper** hi!" in resident.engine.prompts[0]


def test_declined_message_is_handled_and_logged_but_no_turn(resident, chat, monkeypatch):
    from resident import llm
    monkeypatch.setattr(llm, "complete",
                        lambda *a, **k: json.dumps({"decision": "NO", "reason": "chatter"}))
    m = chat.say("ember", "nice weather", "lounge", "general")
    resident.handle_message(m)
    assert resident.engine.prompts == []
    assert m["id"] in resident.handled
    assert "nice weather" in resident.state.topic_history("lounge", "general", 10_000)


def test_declined_messages_reach_the_next_turn(resident, chat, monkeypatch):
    from resident import llm
    monkeypatch.setattr(llm, "complete",
                        lambda *a, **k: json.dumps({"decision": "NO", "reason": "chatter"}))
    resident.handle_message(chat.say("ember", "@**Juniper** first", "lounge", "general"))
    resident.handle_message(chat.say("ember", "an aside nobody answered", "lounge", "general"))
    resident.handle_message(chat.say("ember", "@**Juniper** second", "lounge", "general"))
    last = resident.engine.prompts[-1]
    assert "an aside nobody answered" in last
    assert "your judge didn't wake you" in last


def test_quiet_turn_posts_nothing(resident, chat):
    resident.engine.reply = ""
    resident.handle_message(chat.say("ember", "@**Juniper** you there?", "lounge", "general"))
    assert not [x for x in chat.messages if x["sender_full_name"] == "Juniper"]


def test_own_messages_never_trigger(resident, chat):
    resident.handle_message(chat.say("Juniper", "@**Juniper** talking to myself"))
    assert resident.engine.prompts == []


def test_handled_survives_restart(resident, state):
    from resident.memory.handled import HandledSet
    resident.handled.mark(4242)
    assert 4242 in HandledSet(state)
