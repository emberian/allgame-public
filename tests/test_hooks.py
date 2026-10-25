"""Profile hooks: a foreign endpoint, prompt files, people notes, withheld
terms, the turn archive, and tool modules from outside the harness."""

import json
import sys
import time

from resident import llm, profile
from resident.prompt import Prompts


def configure(prof, **sections):
    for name, values in sections.items():
        for k, v in values.items():
            setattr(getattr(prof, name), k, v)


def test_foreign_endpoint_env_reads_the_key_at_call_time(prof, tmp_path):
    key = tmp_path / "key"
    key.write_text("sk-first\n")
    configure(prof, engine={"base_url": "https://llm.example/coding", "key_file": str(key)})
    env = llm.sdk_env("k3")
    assert env["ANTHROPIC_BASE_URL"] == "https://llm.example/coding"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-first"
    assert env["ANTHROPIC_API_KEY"] == "" and env["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    assert env["ANTHROPIC_MODEL"] == "k3"
    key.write_text("sk-rotated\n")
    assert llm.sdk_env("k3")["ANTHROPIC_AUTH_TOKEN"] == "sk-rotated"


def test_default_endpoint_leaves_credentials_alone(prof):
    env = llm.sdk_env("claude-opus-5-5")
    assert "ANTHROPIC_BASE_URL" not in env and "ANTHROPIC_AUTH_TOKEN" not in env


def test_session_lifetime_comes_from_the_profile(prof):
    from resident.memory.sessions import Sessions
    configure(prof, engine={"session_ttl_minutes": 30})
    s = Sessions()
    session, _ = s.get_or_create("lounge", "general")
    session.last_active = time.time() - 29 * 60
    assert s.get("lounge", "general") is session
    session.last_active = time.time() - 31 * 60
    assert s.get("lounge", "general") is None


def test_named_files_and_people_notes_are_loaded(prof, state):
    configure(prof, prompt={"files": ["sysadmin_inbox.md"], "file_chars": 50})
    state.write_file("sysadmin_inbox.md", "old news " * 20 + "NEWEST LINE")
    state.write_file("people/ember.md", "likes rust and tulips")
    state.write_file("people/rowan.md", "not in this room")
    system = Prompts(state).system("lounge", "general", cold=True, people={"ember"})
    assert "NEWEST LINE" in system and system.count("old news") < 20
    assert "likes rust and tulips" in system and "not in this room" not in system


def test_withheld_terms_never_reach_the_model(prof, state, resident, chat):
    configure(prof, prompt={"scrub_terms": ["Kryptonite"]})
    state.log_message("lounge", "general", "ember", "a fine day", 1_700_000_000, 1)
    state.log_message("lounge", "general", "ember", "about kryptonite", 1_700_000_001, 2)
    history = state.topic_history("lounge", "general", 10_000)
    assert "a fine day" in history and "kryptonite" not in history.lower()
    resident.handle_message(chat.say("ember", "@**Juniper** what about Kryptonite?"))
    prompt = resident.engine.prompts[-1]
    assert "what about […]?" in prompt and "kryptonite" not in prompt.lower()
    log = state.read_file("channels/lounge/general.jsonl")
    assert "what about Kryptonite?" in log                 # the log keeps it verbatim


def test_turns_are_archived(prof, state):
    from resident.engine import Engine
    from resident.memory.sessions import Session

    class R:
        pass
    r = R()
    r.state = state
    e = Engine.__new__(Engine)
    e.r = r
    s = Session("lounge", "general", sdk_session_id="sess-1")
    e._archive("lounge>general", s, "hello", "I should say hello", ["mcp__resident__send_message"])
    row = json.loads(state.read_file("api_archive.jsonl"))
    assert row["stream"] == "lounge" and row["thinking"] == "I should say hello"
    assert row["tools"] == ["mcp__resident__send_message"] and row["session"] == "sess-1"


def test_profile_modules_register_tools(prof, tmp_path, monkeypatch, state, chat):
    (tmp_path / "private_ext.py").write_text(
        "from resident.tools import registry\n"
        "registry.register('ping', 'private', 'Answers pong.', "
        "{'type': 'object', 'properties': {}, 'required': []}, "
        "lambda args, ctx: {'content': 'pong'})\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    prof.modules = ["private_ext"]
    prof.tools = list(prof.tools) + ["private"]
    from resident.tools import registry
    assert "ping" in {t.name for t in registry.enabled(profile.current().tools)}
    ctx = registry.Context(state=state, chat=chat, stream="lounge", topic="general")
    assert registry.run("ping", {}, ctx)["content"] == "pong"
    sys.modules.pop("private_ext", None)


def test_profile_sections_parse(tmp_path):
    (tmp_path / "profile.toml").write_text(f'''
[resident]
name = "Zoë"
state_dir = "{tmp_path / 'state'}"
[engine]
base_url = "https://llm.example/coding"
key_file = "~/.example-key"
session_ttl_minutes = 30
[prompt]
files = ["sysadmin_inbox.md"]
scrub_terms = ["x"]
[mcp.search]
type = "http"
url = "https://search.example/mcp"
[tools]
enabled = ["state"]
modules = ["private_ext"]
''')
    p = profile.load(tmp_path)
    assert p.engine.session_ttl_minutes == 30 and p.prompt.files == ["sysadmin_inbox.md"]
    assert p.mcp["search"]["url"] == "https://search.example/mcp"
    assert p.modules == ["private_ext"]
