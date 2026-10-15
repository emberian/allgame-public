from resident.memory import notes
from resident.memory.sessions import Session
from resident.prompt import Prompts


def ended_session():
    s = Session("lounge", "general")
    s.participants.add("ember")
    s.transcript += [{"role": "room", "text": "ember: can you remind me friday?"},
                     {"role": "resident", "text": "I'll set a timer."}]
    return s


def test_note_is_written_and_loaded_on_a_cold_start(state, monkeypatch):
    from resident import llm
    monkeypatch.setattr(llm, "complete",
                        lambda *a, **k: "I promised ember a reminder on Friday.")
    notes.write(state, ended_session())
    system = Prompts(state).system("lounge", "general", cold=True)
    assert "I promised ember a reminder on Friday." in system


def test_failed_model_call_leaves_a_stub(state, monkeypatch):
    from resident import llm

    def boom(*a, **k):
        raise RuntimeError("no quota")
    monkeypatch.setattr(llm, "complete", boom)
    notes.write(state, ended_session())
    text = state.read_file("sessions/lounge__general.md")
    assert "No note could be written" in text and "ember" in text


def test_sessions_without_talk_get_no_note(state, monkeypatch):
    from resident import llm
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "should not be called")
    notes.write(state, Session("lounge", "general"))
    assert state.read_file("sessions/lounge__general.md") is None
