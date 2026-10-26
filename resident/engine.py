"""Turns, through the Claude Agent SDK.

One turn: a system prompt, the new messages, and the resident's tools
(served from an in-process MCP server built from the tool registry). The SDK
session id is kept on the room's Session, so the next turn in that room
resumes the same conversation and its prompt cache.

PERSISTENT ROOMS (profile chat.persistent) keep one SDK session for good:
across days, session expiry and restarts. A persistent room is BORN on its
first turn with the room's history in the prompt, and REBORN — retired and
born again from the logs — once it grows past the profile's ceiling. Long
contexts flatten a voice and cost more every turn; rebirth keeps the talk
and drops the machinery (tool results, internal monologue). If the provider
refuses a persistent room's context, it is retired and retried once fresh.

Three guards around every turn:
  - one turn at a time (tools act on the turn's own context);
  - a near-identical second send in the same turn is refused;
  - a turn that produced text but posted nothing gets one check-in — the
    text may have been meant for the room — and "noop" is a fine answer.
"""

import asyncio
import dataclasses
import difflib
import json
import logging
import threading
import time
from datetime import datetime, timezone

from resident import llm, profile
from resident.tools import registry, surface

logger = logging.getLogger("engine")

BINDINGS = "persistent_rooms.json"

CHECK_IN = ("[HARNESS] Just checking: your last turn ended in plain text, which "
            "nobody in the room can see. If some of it was meant for them, send "
            "it with send_message (or add_reaction). If it was private thinking, "
            "or you chose to stay quiet, reply exactly: noop")


def _mcp(result: dict) -> dict:
    return {"content": [{"type": "text", "text": str(result.get("content", ""))}],
            **({"is_error": True} if result.get("is_error") else {})}


class Engine:
    def __init__(self, resident):
        self.r = resident
        self.ctx: registry.Context | None = None
        self.sends: list[str] = []
        self.visible = False
        self.context_tokens = 0
        self.refused: str | None = None
        self.last_progress = 0.0
        self.lock = threading.Lock()
        self.tools = registry.enabled(profile.current().tools)
        self.server = self._build_server()

    # ---- tools ----------------------------------------------------------------

    def _build_server(self):
        from claude_agent_sdk import create_sdk_mcp_server, tool as sdk_tool

        def make(entry):
            @sdk_tool(entry["name"], entry["description"], entry["schema"])
            async def handler(args):
                t, args, err = surface.resolve(entry, args)
                if err:
                    return _mcp({"content": err, "is_error": True})
                if t.name in ("send_message", "send_dm"):
                    content = str(args.get("content", ""))
                    if content and any(difflib.SequenceMatcher(None, p, content).ratio() > 0.85
                                       for p in self.sends):
                        return _mcp({"content": "You already posted essentially this "
                                     "reply this turn, and it landed. Don't re-send.",
                                     "is_error": True})
                self.last_progress = time.time()
                result = await asyncio.to_thread(registry.run, t.name, args, self.ctx)
                self.last_progress = time.time()
                if not result.get("is_error"):
                    if t.name in ("send_message", "send_dm"):
                        self.sends.append(str(args.get("content", "")))
                    if t.name in registry.VISIBLE and "Held as a draft" not in result["content"]:
                        self.visible = True
                return _mcp(result)
            return handler
        # One tool per group (state, search, timer …), `op` picking the action.
        self.surface = surface.surface(self.tools)
        return create_sdk_mcp_server(name="resident", tools=[make(e) for e in self.surface])

    # ---- the archive ------------------------------------------------------------

    def _archive(self, key, session, said, thought, tools):
        """One line per model run in api_archive.jsonl: what the resident
        thought, said and called — the only record of its thinking. Never
        trimmed (the log is the resident's own history)."""
        try:
            p = profile.current()
            stream, topic = key.split(">", 1)
            self.r.state.append_file("api_archive.jsonl", json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(), "key": key,
                "stream": stream, "topic": topic, "sender": p.name,
                "engine": "agent_sdk", "model": p.models.main,
                "session": session.sdk_session_id, "tools": tools,
                "thinking": thought[-20_000:], "text": said[-8_000:]},
                ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"archive write failed: {e}")

    # ---- persistent rooms -----------------------------------------------------

    def _bindings(self) -> dict:
        try:
            return json.loads(self.r.state.read_file(BINDINGS) or "{}")
        except ValueError:
            return {}

    def _bind(self, key: str, **fields):
        b = self._bindings()
        b.setdefault(key, {}).update(fields)
        self.r.state.write_file(BINDINGS, json.dumps(b, indent=1))

    @staticmethod
    def persistent(stream: str, topic: str) -> bool:
        return f"{stream}>{topic}" in profile.current().chat.persistent

    # ---- a turn ------------------------------------------------------------------

    def respond(self, session, stream, topic, sender, trigger, prompt_text,
                quiet: bool = False) -> tuple[str, list]:
        """Run one turn. Returns (the resident's private text, posted ids).
        quiet=True for turns the resident took on its own (timers, solitude):
        no check-in when it ends without posting."""
        with self.lock:
            return self._respond(session, stream, topic, sender, trigger,
                                 prompt_text, quiet)

    def _respond(self, session, stream, topic, sender, trigger, prompt_text, quiet):
        p = profile.current()
        key = f"{stream}>{topic}"
        birth = ""
        if self.persistent(stream, topic) and not session.sdk_session_id:
            bound = self._bindings().get(key) or {}
            if bound.get("sdk_session_id") and \
                    bound.get("context_tokens", 0) <= p.limits.persistent_session_max_tokens:
                session.sdk_session_id = bound["sdk_session_id"]
            else:
                if bound.get("sdk_session_id"):
                    logger.info(f"Rebirth for {key} ({bound.get('context_tokens'):,} tokens)")
                birth = self.r.prompts.birth(stream, topic)
        cold = not session.sdk_session_id
        system = self.r.prompts.system(stream, topic, cold=cold and not birth,
                                       people=session.participants)
        text = (birth + "\n\n" if birth else "") + prompt_text
        if not quiet:
            text += "\n\n[send_message to reply]"

        self.sends, self.visible, self.context_tokens, self.refused = [], False, 0, None
        self.last_progress = time.time()   # heartbeat for the typing indicator
        self.ctx = registry.Context(state=self.r.state, chat=self.r.chat, stream=stream,
                                    topic=topic, sender=sender, message=trigger or {})
        from claude_agent_sdk import ClaudeAgentOptions
        opts = ClaudeAgentOptions(
            system_prompt=system, model=p.models.main, fallback_model=p.models.fallback,
            max_turns=self.r.max_tool_turns,
            mcp_servers={**p.mcp, "resident": self.server},
            tools=[], allowed_tools=[f"mcp__resident__{e['name']}" for e in self.surface]
            + [f"mcp__{name}" for name in p.mcp],
            disallowed_tools=list(p.disallowed_tools),
            permission_mode="bypassPermissions", setting_sources=[],
            resume=session.sdk_session_id or None, cwd=str(llm.sdk_home()),
            max_buffer_size=32_000_000, env=llm.sdk_env(p.models.main))
        try:
            try:
                said = asyncio.run(self._run(text, opts, session, key))
            except Exception:
                if not (self.refused and self.persistent(stream, topic)):
                    raise
                logger.warning(f"Provider refused {key}'s context; reborn and retrying")
                self._bind(key, sdk_session_id="", context_tokens=0)
                session.sdk_session_id = ""
                text = self.r.prompts.birth(stream, topic) + "\n\n" + prompt_text
                said = asyncio.run(self._run(text, dataclasses.replace(opts, resume=None),
                                             session, key))
            if not quiet and not self.visible and said.strip():
                logger.info(f"Turn in #{key} ended in text only — checking in")
                more = asyncio.run(self._run(
                    CHECK_IN, dataclasses.replace(opts, resume=session.sdk_session_id or None),
                    session, key))
                said += "\n" + more
        finally:
            session.sent_word_counts += self.ctx.sent_word_counts
            if self.ctx.restart_requested:
                self.r.restart_requested = True
            posted = list(self.ctx.posted_ids)
            self.ctx = None
        session.transcript.append({"role": "resident", "text": said})
        if self.persistent(stream, topic) and session.sdk_session_id:
            self._bind(key, sdk_session_id=session.sdk_session_id,
                       context_tokens=self.context_tokens)
        return said, posted

    async def _run(self, text, opts, session, key) -> str:
        from claude_agent_sdk import AssistantMessage, ResultMessage, query
        said, thought, tools = [], [], []
        async for msg in query(prompt=text, options=opts):
            self.last_progress = time.time()
            if isinstance(msg, AssistantMessage):
                for b in msg.content:
                    if getattr(b, "thinking", None):
                        thought.append(b.thinking)
                    elif getattr(b, "name", None):
                        tools.append(b.name)
                    elif getattr(b, "text", None) is not None:
                        said.append(b.text)
                # One model call's context, never the turn's summed usage.
                u = getattr(msg, "usage", None)
                if isinstance(u, dict):
                    self.context_tokens = max(self.context_tokens, sum(
                        u.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens",
                                              "cache_creation_input_tokens")))
            elif isinstance(msg, ResultMessage):
                if msg.session_id:
                    session.sdk_session_id = msg.session_id
                self._archive(key, session, "\n".join(said), "\n".join(thought), tools)
                logger.info(f"Turn done in #{key}: {msg.num_turns} steps, "
                            f"{msg.duration_api_ms}ms")
                if msg.is_error:
                    if llm.is_refusal(str(msg.result)):
                        self.refused = str(msg.result)
                    raise RuntimeError(f"turn failed: {msg.result!r}")
        return "\n".join(said)
