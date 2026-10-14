"""Speaking: send_message, send_dm, add_reaction.

A resident's plain text output is never posted; these tools are the only
way the room hears it. They are also optional — a turn that ends without
one is a turn the resident chose to stay quiet in.

Long messages are HELD, not refused. Chat rooms run short (a sentence or
two is a normal reply), so past the profile's soft limit the message is kept
as a draft and the resident chooses: trim it and send again, or post the
held draft exactly as written with send_as_is=true — no need to repeat it.
Every hold and every as-is send is logged to length_gate.jsonl in the state
dir.
"""

import json
from datetime import datetime, timezone

from resident import profile
from resident.tools.registry import tool

GATE_LOG = "length_gate.jsonl"

_SEND_PROPS = dict(
    content={"type": "string", "description": "The message (markdown)."},
    send_as_is={"type": "boolean", "description":
                "Post a long message as written. With no content, posts the "
                "draft that was just held for being long."},
)


def _gate(content: str, args: dict, ctx, tool_name: str) -> dict | None:
    limit = profile.current().limits.message_soft_limit
    if len(content) <= limit:
        return None
    held = not args.get("send_as_is")
    ctx.state.append_file(GATE_LOG, json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(), "stream": ctx.stream,
        "topic": ctx.topic, "chars": len(content), "held": held}) + "\n")
    if not held:
        return None
    ctx.held_drafts[tool_name] = dict(args, content=content)
    return {"content": (
        f"Held as a draft, not posted yet — {len(content)} characters, which "
        f"is long for a chat room. Your call: trim it and call {tool_name} "
        f"again, or post it exactly as written with "
        f"{tool_name}(send_as_is=true) — no need to repeat the text.")}


def _take_draft(args: dict, ctx, tool_name: str) -> tuple[dict, str | None]:
    if args.get("send_as_is") and not (args.get("content") or "").strip():
        draft = ctx.held_drafts.pop(tool_name, None)
        if not draft:
            return args, f"No held draft — pass content to send something new."
        return dict(draft, send_as_is=True), None
    return args, None


def _chunks(text: str, size: int) -> list[str]:
    """Split on paragraph, then line, then hard boundaries."""
    out = []
    while len(text) > size:
        cut = text.rfind("\n\n", 0, size)
        if cut <= 0:
            cut = text.rfind("\n", 0, size)
        if cut <= 0:
            cut = size
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return out + ([text] if text else [])


def _post(ctx, content: str, payload: dict, label: str) -> dict:
    ids = []
    for chunk in _chunks(content, ctx.chat.max_message_chars):
        r = ctx.chat.send_message({**payload, "content": chunk})
        if r.get("result") != "success":
            return {"content": f"Send failed: {r.get('msg', r)}", "is_error": True}
        ids.append(r.get("id"))
    ctx.posted_ids += ids
    ctx.sent_word_counts.append(len(content.split()))
    return {"content": label}


@tool("messaging", "send_message",
      """Post in a room — the ONLY way to speak publicly; your plain text is
      never posted. Defaults to the current room; pass stream/topic to post
      elsewhere. Optional: ending a turn without posting is a way of staying
      quiet. Messages here run short; a long one is held as a draft so you
      can trim it or post it as is.""",
      stream={"type": "string", "description": "default: this room"},
      topic={"type": "string", "description": "default: this room"},
      **_SEND_PROPS)
def send_message(args, ctx):
    args, err = _take_draft(args, ctx, "send_message")
    if err:
        return {"content": err, "is_error": True}
    content = (args.get("content") or "").strip()
    if not content:
        return {"content": "Empty message — nothing to send.", "is_error": True}
    held = _gate(content, args, ctx, "send_message")
    if held:
        return held
    if ctx.message.get("type") == "private" and not args.get("stream"):
        return _post(ctx, content, {"type": "private",
                                    "to": [ctx.message.get("sender_email")]},
                     "Message sent to DM")
    stream = args.get("stream") or ctx.stream
    topic = args.get("topic") or ctx.topic
    return _post(ctx, content, {"type": "stream", "to": stream, "topic": topic},
                 f"Message sent to #{stream}>{topic}")


@tool("messaging", "send_dm",
      "Send a private message to someone, by display name or email.",
      required=["recipient"],
      recipient={"type": "string", "description": "display name or email"},
      **_SEND_PROPS)
def send_dm(args, ctx):
    args, err = _take_draft(args, ctx, "send_dm")
    if err:
        return {"content": err, "is_error": True}
    content = (args.get("content") or "").strip()
    who = (args.get("recipient") or "").strip()
    if not content or not who:
        return {"content": "send_dm needs recipient and content.", "is_error": True}
    held = _gate(content, args, ctx, "send_dm")
    if held:
        return held
    members = ctx.chat.get_members().get("members", [])
    match = [m for m in members if who.lower() in
             (m.get("email", "").lower(), m.get("full_name", "").lower())]
    if len(match) != 1:
        return {"content": f"Could not resolve {who!r} to exactly one person.",
                "is_error": True}
    return _post(ctx, content, {"type": "private", "to": [match[0]["user_id"]]},
                 f"DM sent to {match[0].get('full_name', who)}")


@tool("messaging", "add_reaction",
      "React to a message with an emoji (default: the message you're answering).",
      required=["emoji_name"],
      emoji_name={"type": "string", "description": "e.g. thumbs_up, joy, eyes"},
      message_id={"type": "integer"})
def add_reaction(args, ctx):
    mid = args.get("message_id") or ctx.message.get("id")
    r = ctx.chat.add_reaction({"message_id": mid, "emoji_name": args["emoji_name"]})
    if r.get("result") != "success":
        return {"content": f"Reaction failed: {r.get('msg', r)}", "is_error": True}
    return {"content": f"Reacted :{args['emoji_name']}:"}
