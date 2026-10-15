"""Every tool, declared once: its schema sits on the function that runs it.

    @tool("state", "read_state_file", "Read a file from your state directory.",
          path={"type": "string"}, required=["path"])
    def read_state_file(args, ctx): ...

A tool belongs to a GROUP; a profile enables groups (profile.tools), so what
a resident can do is configuration, not a separate list kept in sync by hand.

Handlers receive (args, ctx) and return {"content": str} or
{"content": str, "is_error": True}. An exception becomes an error result —
a broken tool must never break the turn.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("tools")


@dataclass
class Tool:
    name: str
    group: str
    description: str
    schema: dict
    handler: callable


TOOLS: dict[str, Tool] = {}

# A turn with none of these succeeding said nothing the room could see.
VISIBLE = {"send_message", "send_dm", "add_reaction"}


def tool(group: str, name: str, description: str, required=(), **properties):
    def deco(fn):
        TOOLS[name] = Tool(name, group, " ".join(description.split()), {
            "type": "object", "properties": properties,
            "required": list(required)}, fn)
        return fn
    return deco


@dataclass
class Context:
    """Everything a tool may touch during one turn."""
    state: object
    chat: object
    stream: str
    topic: str
    sender: str = ""
    message: dict = field(default_factory=dict)
    posted_ids: list = field(default_factory=list)
    sent_word_counts: list = field(default_factory=list)
    held_drafts: dict = field(default_factory=dict)
    restart_requested: bool = False


MODULES = ("messaging", "state_files", "search", "web", "games", "timers")


def load_all():
    """Import every tool module so its @tool declarations register."""
    import importlib
    for m in MODULES:
        importlib.import_module(f"resident.tools.{m}")


def enabled(groups: list[str]) -> list[Tool]:
    load_all()
    return [t for t in TOOLS.values() if t.group in groups]


def run(name: str, args: dict, ctx: Context) -> dict:
    load_all()
    t = TOOLS.get(name)
    if not t:
        return {"content": f"No tool named {name}.", "is_error": True}
    try:
        return t.handler(args or {}, ctx)
    except Exception as e:
        logger.warning(f"tool {name} failed: {e}", exc_info=True)
        return {"content": f"{name} failed: {e}", "is_error": True}
