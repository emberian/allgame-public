"""One-shot model calls for the harness's own machinery: the judge, session
notes. No tools, no session — one prompt in, text out.

These go through the Agent SDK, the same door the resident's turns use, so
they share its credentials and limits (whatever ANTHROPIC_API_KEY or
CLAUDE_CODE_OAUTH_TOKEN the environment provides).
"""

import asyncio
import logging
from pathlib import Path

from resident import profile

logger = logging.getLogger("llm")


class PolicyRefusal(RuntimeError):
    """The provider refused the request itself. Retrying the same payload is
    pointless; the caller has to send something different."""


REFUSAL_MARKERS = ("usage policy", "violate", "policy violation",
                   "unable to respond to this request")


def is_refusal(text: str) -> bool:
    t = (text or "").lower()
    return any(m in t for m in REFUSAL_MARKERS)


def sdk_home():
    """The Agent SDK's config dir: inside the resident's own state, so it
    never reads the host user's Claude Code settings or memory."""
    p = profile.current().state_dir / ".claude"
    p.mkdir(parents=True, exist_ok=True)
    return p


def sdk_env(model: str) -> dict:
    """The environment every Agent SDK call runs with. For a foreign
    endpoint: its URL and key, and no Anthropic credentials that would
    outrank them."""
    p = profile.current()
    env = {"CLAUDE_CONFIG_DIR": str(sdk_home())}
    if p.engine.base_url:
        key = ""
        if p.engine.key_file:
            key = Path(p.engine.key_file).expanduser().read_text().strip()
        env.update(ANTHROPIC_BASE_URL=p.engine.base_url, ANTHROPIC_AUTH_TOKEN=key,
                   ANTHROPIC_API_KEY="", CLAUDE_CODE_OAUTH_TOKEN="",
                   ANTHROPIC_MODEL=model,
                   ANTHROPIC_SMALL_FAST_MODEL=p.models.judge)
    return env


async def _run(prompt: str, system: str, model: str) -> str:
    from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions,
                                  ResultMessage, query)
    options = ClaudeAgentOptions(
        system_prompt=system or None, model=model, max_turns=1, tools=[],
        permission_mode="bypassPermissions", setting_sources=[],
        cwd=str(sdk_home()), env=sdk_env(model))
    parts, failure = [], None
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            parts += [b.text for b in msg.content if getattr(b, "text", None)]
        elif isinstance(msg, ResultMessage) and msg.is_error:
            failure = str(msg.result)
    if failure:
        raise (PolicyRefusal if is_refusal(failure) else RuntimeError)(failure)
    return "\n".join(parts).strip()


def complete(prompt: str, system: str = "", model: str | None = None) -> str:
    return asyncio.run(_run(prompt, system, model or profile.current().models.memory))
