"""The resident's own memory: plain files in its state directory.

All paths are relative to the state dir and cannot leave it.
"""

import re

from resident.tools.registry import tool

MAX_READ = 60_000


@tool("state", "read_state_file", "Read a file from your state directory.",
      required=["path"], path={"type": "string", "description": "e.g. scratchpad.md, people/ana.md"})
def read_state_file(args, ctx):
    text = ctx.state.read_file(args["path"])
    if text is None:
        return {"content": f"No file {args['path']}.", "is_error": True}
    if len(text) > MAX_READ:
        return {"content": text[-MAX_READ:] + f"\n\n[showing the last {MAX_READ} of {len(text)} chars]"}
    return {"content": text or "(empty)"}


@tool("state", "write_state_file",
      "Create or overwrite a file in your state directory.",
      required=["path", "content"], path={"type": "string"},
      content={"type": "string"})
def write_state_file(args, ctx):
    ctx.state.write_file(args["path"], args["content"])
    return {"content": f"Wrote {args['path']} ({len(args['content'])} chars)."}


@tool("state", "edit_state_file",
      """Edit a file in place: replace old_string with new_string (exactly one
      match, unless replace_all). For several edits at once pass edits=[{old_string,
      new_string, replace_all?}].""",
      required=["path"], path={"type": "string"}, old_string={"type": "string"},
      new_string={"type": "string"}, replace_all={"type": "boolean"},
      edits={"type": "array", "items": {"type": "object"}})
def edit_state_file(args, ctx):
    text = ctx.state.read_file(args["path"])
    if text is None:
        return {"content": f"No file {args['path']}.", "is_error": True}
    edits = args.get("edits") or [{k: args.get(k) for k in
                                   ("old_string", "new_string", "replace_all")}]
    for i, e in enumerate(edits, 1):
        old, new = e.get("old_string") or "", e.get("new_string") or ""
        n = text.count(old) if old else 0
        if n == 0:
            return {"content": f"Edit {i}: old_string not found — nothing changed.", "is_error": True}
        if n > 1 and not e.get("replace_all"):
            return {"content": f"Edit {i}: old_string matches {n} times; make it "
                    "unique or pass replace_all — nothing changed.", "is_error": True}
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    ctx.state.write_file(args["path"], text)
    return {"content": f"Edited {args['path']} ({len(edits)} edit(s))."}


@tool("state", "list_state_files", "List files in a directory of your state.",
      directory={"type": "string", "description": "'' for the top level"})
def list_state_files(args, ctx):
    d = ctx.state.path(args.get("directory") or "")
    if not d.is_dir():
        return {"content": f"No directory {args.get('directory')}.", "is_error": True}
    rows = []
    for p in sorted(d.iterdir()):
        if p.name.startswith("."):
            continue
        rows.append(f"{p.name}/" if p.is_dir() else f"{p.name}  ({p.stat().st_size} bytes)")
    return {"content": "\n".join(rows) or "(empty)"}


@tool("state", "grep_state", "Search your state files with a regex.",
      required=["pattern"], pattern={"type": "string"},
      directory={"type": "string", "description": "limit to a subdirectory"})
def grep_state(args, ctx):
    rx = re.compile(args["pattern"], re.I)
    root = ctx.state.path(args.get("directory") or "")
    hits = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix not in (".md", ".txt", ".json", ".jsonl") \
                or any(part.startswith(".") for part in p.relative_to(ctx.state.root).parts):
            continue
        for n, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            if rx.search(line):
                hits.append(f"{p.relative_to(ctx.state.root)}:{n}: {line[:200]}")
                if len(hits) >= 100:
                    return {"content": "\n".join(hits) + "\n(stopped at 100)"}
    return {"content": "\n".join(hits) or "No matches."}
