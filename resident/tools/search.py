"""Searching what has been said, from the resident's own channel logs."""

import json
import re
from datetime import datetime

from resident.tools.registry import tool
from resident.util import fmt_log_time


@tool("search", "search_messages",
      """Search every message you have seen (your channel logs), newest first.
      Filter by stream, topic or sender.""",
      required=["query"], query={"type": "string", "description": "regex"},
      stream={"type": "string"}, topic={"type": "string"},
      sender={"type": "string"}, limit={"type": "integer"})
def search_messages(args, ctx):
    rx = re.compile(args["query"], re.I)
    limit = max(1, min(int(args.get("limit") or 20), 100))
    base = ctx.state.root / "channels"
    files = sorted(base.rglob("*.jsonl"))
    hits = []
    for f in files:
        rel = f.relative_to(base).parts
        if args.get("stream") and rel[0].lower() != args["stream"].lower():
            continue
        for line in f.read_text(errors="ignore").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if args.get("topic") and r.get("topic", "").lower() != args["topic"].lower():
                continue
            if args.get("sender") and args["sender"].lower() not in r.get("sender", "").lower():
                continue
            if rx.search(r.get("content", "")):
                hits.append((r["ts"], rel[0], r))
    hits.sort(key=lambda h: h[0], reverse=True)
    out = [f"[{fmt_log_time(datetime.fromisoformat(ts).timestamp())}] "
           f"#{s}>{r.get('topic')} {r.get('sender')}: {r.get('content', '')[:300]}"
           for ts, s, r in hits[:limit]]
    return {"content": f"{len(hits)} match(es).\n" + "\n".join(out) if out else "No matches."}
