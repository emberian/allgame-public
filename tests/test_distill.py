import json

import pytest

from resident import llm
from resident.memory import distill
from resident.prompt import Prompts


@pytest.fixture
def room(state):
    for i in range(distill.RAW_KEEP + 150):
        word = "FORBIDDEN" if i == 40 else f"line {i}"
        state.log_message("lounge", "general", "ember", word, 1_700_000_000 + i, i + 1)
    return "channels/lounge/general.jsonl"


def stub(seen):
    def complete(prompt, system="", model=None):
        seen.append(prompt)
        if "FORBIDDEN" in prompt:
            raise llm.PolicyRefusal("refused")
        n = prompt.count("ember:")
        return f"digest covering {n} new messages"
    return complete


def test_folds_aged_history_and_loads_it(state, room, monkeypatch):
    monkeypatch.setattr(distill, "_render", lambda r: f"ember: {r['content']}")
    monkeypatch.setattr(llm, "complete", stub([]))
    state.write_file("channels/lounge/general.jsonl",
                     state.read_file(room).replace("FORBIDDEN", "fine"))
    assert distill.distill_log(state, room)
    mem = json.loads(state.read_file(distill.memory_path(room)))
    assert mem["through"] == 150 and "150" in mem["memory"]
    assert not distill.distill_log(state, room)            # nothing new has aged
    system = Prompts(state).system("lounge", "general", cold=True)
    assert "<room_memory" in system and "digest covering 150" in system


def test_a_refused_message_is_skipped_not_retried_forever(state, room, monkeypatch):
    monkeypatch.setattr(distill, "_render", lambda r: f"ember: {r['content']}")
    seen = []
    monkeypatch.setattr(llm, "complete", stub(seen))
    distill.distill_log(state, room)
    mem = json.loads(state.read_file(distill.memory_path(room)))
    assert [s["msg_id"] for s in mem["skipped"]] == [41]     # line 40 -> msg id 41
    assert mem["through"] == 41 and "40" in mem["memory"]   # the accepted prefix
    assert "FORBIDDEN" in state.read_file(room)              # the log is untouched


def test_sealed_eras(state, monkeypatch):
    monkeypatch.setattr(distill, "ERA_SPAN", 100)
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "a digest")
    for i in range(distill.RAW_KEEP + 120):
        state.log_message("lounge", "eras", "ember", f"m{i}", 1_700_000_000 + i, i + 1)
    distill.distill_log(state, "channels/lounge/eras.jsonl")
    mem = json.loads(state.read_file("channels/lounge/eras.memory.json"))
    assert len(mem["eras"]) == 1 and mem["memory"] == "" and mem["head_from"] == 120
    assert "Volume 1" in distill.room_memory(state, "lounge", "eras")
