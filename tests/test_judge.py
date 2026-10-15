import json

import pytest

from resident.judge import Judge, parse_verdict


def msg(content, sender="ember", stream="lounge", private=False):
    return {"id": 1, "content": content, "sender_full_name": sender,
            "type": "private" if private else "stream",
            "display_recipient": stream, "subject": "general"}


@pytest.fixture
def judge(state):
    return Judge(state)


def test_certainties(judge):
    assert judge.certain(msg("hi", private=True), "dm") == "DM"
    assert judge.certain(msg("@**Juniper** hi"), "lounge") == "named"
    assert judge.certain(msg("@**Juniper** hi", stream="quiet"), "quiet") == "named"


def test_no_certainty(judge):
    assert judge.certain(msg("just chatting"), "lounge") is None
    assert judge.certain(msg("hi", stream="quiet"), "quiet") is None        # mention-only
    assert judge.certain(msg("@**Juniper** hi", stream="elsewhere"), "elsewhere") is None
    assert judge.certain(msg("@**Juniper** echo", sender="Juniper"), "lounge") is None


def test_model_path_never_runs_outside_rooms(judge, monkeypatch):
    from resident import llm
    monkeypatch.setattr(llm, "complete", lambda *a, **k: pytest.fail("model called"))
    assert not judge.worth_a_turn(msg("hi", stream="quiet"), "quiet")
    assert not judge.worth_a_turn(msg("hi", stream="elsewhere"), "elsewhere")


@pytest.mark.parametrize("decision", ["YES", "NO"])
def test_model_verdict_and_remarks(judge, monkeypatch, decision):
    from resident import llm
    verdict = {"decision": decision, "reason": "test", "remark": "watch this one"}
    monkeypatch.setattr(llm, "complete", lambda *a, **k: json.dumps(verdict))
    assert judge.worth_a_turn(msg("anyone around?"), "lounge") == (decision == "YES")
    if decision == "YES":
        assert judge.last_remark == "watch this one"
    else:
        # A declined message's remark waits for the next turn in that room.
        assert [n["remark"] for n in judge.drain_remarks("lounge", "general")] == \
            ["watch this one"]


def test_parse_verdict():
    assert parse_verdict('noise {"decision": "yes"} noise')["decision"] == "YES"
    with pytest.raises(ValueError):
        parse_verdict('{"decision": "maybe"}')
    with pytest.raises(ValueError):
        parse_verdict("no json here")


def test_lurk_decay(judge):
    assert judge.lurk("lounge")[0] == "active"
    for _ in range(40):
        judge.record("lounge", direct=False)
    assert judge.lurk("lounge")[0] != "active"
    judge.record("lounge", direct=True)
    assert judge.lurk("lounge") == ("active", 0)
