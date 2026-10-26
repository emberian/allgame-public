"""What the model sees: one tool per kind of thing, `op` choosing the action.

Tools are registered one per action (read_state_file, set_timer, …), which
keeps each implementation small. The model is shown them grouped — `state`,
`search`, `timer`, … — with `op` naming the action, because a long list of
near-identical tools is harder to choose from than a short list of kinds.

Each action belongs to a group and has a one-line summary for the group's
description. The harness declares its own below; a private module declares
its own with declare() (and describe() for a new group), and a declaration
for a name replaces an earlier one — the same rule as registry.register().
Tools declared nowhere are shown as they are.
"""

# group -> the sentence that opens its description
_GROUPS: dict[str, str] = {
    "state": "Your state directory: your own files.",
    "search": "Search what has been said.",
    "web": "The web.",
    "game": "The shared game table: one board and one 'whose turn' every resident reads. "
            "The table, not your memory, says whose move it is.",
    "timer": "Wake yourself up later with a note to yourself; timers survive restarts.",
    "sandbox": "A throwaway Linux container for running code; /workspace lasts across "
               "calls in this room and your state directory is read-only at /state.",
}

# tool name -> (group, op, one line for the group's description)
_OPS: dict[str, tuple[str, str, str]] = {
    "read_state_file": ("state", "read", "read a file (path)"),
    "write_state_file": ("state", "write", "create or overwrite a file (path, content)"),
    "edit_state_file": ("state", "edit", "replace old_string with new_string in a file "
                        "(path; replace_all, or edits=[…] for several)"),
    "list_state_files": ("state", "list", "list a directory (directory)"),
    "grep_state": ("state", "grep", "search file contents by regex (pattern; directory)"),
    "search_messages": ("search", "room", "the chat server's messages (query; stream, topic, "
                        "sender, limit)"),
    "fetch_url": ("web", "fetch", "fetch a URL (url)"),
    "game_status": ("game", "status", "the game's state and whose turn it is (game)"),
    "game_move": ("game", "move", "record your move and pass the turn; refused when it "
                  "isn't yours (game, summary; board, next_player)"),
    "game_setup": ("game", "setup", "open a game or correct it (game; players, current, "
                   "turn, board, finished)"),
    "set_timer": ("timer", "set", "set a timer (note; in_minutes or at, or every_minutes "
                  "to repeat; watch=true to wake only when the room has news)"),
    "list_timers": ("timer", "list", "your timers"),
    "cancel_timer": ("timer", "cancel", "cancel one (id)"),
    "run_sandbox": ("sandbox", "run", "run a shell command (command; files to write first, "
                    "timeout)"),
}


def describe(group: str, text: str):
    """Set (or replace) the sentence that opens a group's description."""
    _GROUPS[group] = text


def declare(tool_name: str, group: str, op: str, line: str):
    """Show tool_name as `group(op=…)`, summarised by `line`."""
    _OPS[tool_name] = (group, op, line)


def surface(tools) -> list[dict]:
    """[{name, description, schema, ops: {op: tool}}] for registry Tools."""
    out, groups = [], {}
    for t in tools:
        if t.name not in _OPS:
            out.append({"name": t.name, "description": t.description,
                        "schema": t.schema, "ops": None, "tool": t})
            continue
        group, op, line = _OPS[t.name]
        if group not in groups:
            groups[group] = {"name": group, "ops": {}, "lines": [], "props": {}}
            out.append(groups[group])
        g = groups[group]
        g["ops"][op] = t
        g["lines"].append(f"- {op}: {line}")
        for k, v in t.schema.get("properties", {}).items():
            g["props"].setdefault(k, v)
    for g in groups.values():
        g["description"] = (_GROUPS.get(g["name"], "") + "\nop:\n" + "\n".join(g["lines"])).strip()
        g["schema"] = {"type": "object",
                       "properties": {"op": {"type": "string", "enum": list(g["ops"])},
                                      **g.pop("props")},
                       "required": ["op"]}
        del g["lines"]
    return out


def resolve(entry: dict, args: dict):
    """(tool, its arguments, error) for a call to a surfaced entry."""
    if entry["ops"] is None:
        return entry["tool"], dict(args or {}), None
    args = dict(args or {})
    op = str(args.pop("op", "") or "")
    t = entry["ops"].get(op)
    if t is None:
        return None, args, f"{entry['name']}: op must be one of {', '.join(entry['ops'])}."
    args = {k: v for k, v in args.items() if k in t.schema.get("properties", {})}
    missing = [k for k in t.schema.get("required", []) if k not in args]
    if missing:
        return None, args, f"{entry['name']}(op={op}) needs {', '.join(missing)}."
    return t, args, None
