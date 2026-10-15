"""Reading and changing your own harness. See resident/safety/selfedit.py.

Opt in by adding "harness" to a profile's tool groups. The changes are real
code changes in the checkout the resident runs from, committed under its
name; run it under the supervisor (the default outside --console), which
restarts it onto the new code and rolls back an edit that crashes it.
"""

from pathlib import Path

from resident import profile
from resident.tools.registry import tool

MAX_READ = 80_000


def _selfedit():
    from resident.safety.selfedit import for_this_checkout
    return for_this_checkout(profile.current().name)


@tool("harness", "read_harness",
      """Read your own harness: a file in the resident/ package (e.g.
      'judge.py', 'tools/messaging.py'), or '' to list them all.""",
      file={"type": "string"})
def read_harness(args, ctx):
    se = _selfedit()
    file = (args.get("file") or "").strip()
    if not file:
        files = sorted(p.relative_to(se.pkg).as_posix() for p in se.pkg.rglob("*.py"))
        return {"content": "\n".join(files)}
    target = se.pkg / file
    if not se._inside(target) or not target.is_file():
        return {"content": f"No file {file} in resident/.", "is_error": True}
    text = target.read_text()
    return {"content": text[:MAX_READ] + ("" if len(text) <= MAX_READ else
                                          f"\n[first {MAX_READ} of {len(text)} chars]")}


@tool("harness", "edit_harness",
      """Change your own harness code. action='edit' (default): replace
      old_string with new_string in `file` (or several at once with
      edits=[{old_string, new_string, replace_all?}]). action='create': a new
      file with new_string as its contents. action='delete': remove `file`.
      Every change must parse and leave the package importable, or nothing is
      changed. It is committed under your name, and you restart onto it when
      this turn ends; if the new code crashes, it is rolled back and you are
      told. Read the file with read_harness first.""",
      required=["file", "why"],
      file={"type": "string", "description": "path inside resident/, e.g. 'judge.py'"},
      why={"type": "string", "description": "the commit message: what and why"},
      action={"type": "string", "enum": ["edit", "create", "delete"]},
      old_string={"type": "string"}, new_string={"type": "string"},
      replace_all={"type": "boolean"},
      edits={"type": "array", "items": {"type": "object"}})
def edit_harness(args, ctx):
    se = _selfedit()
    action = args.get("action") or "edit"
    file, why = args["file"].strip(), args["why"].strip()
    if action == "create":
        out = se.create(file, args.get("new_string") or "", why)
    elif action == "delete":
        out = se.delete(file, why)
    else:
        edits = args.get("edits") or [{k: args.get(k) for k in
                                       ("old_string", "new_string", "replace_all")}]
        out = se.edit(file, edits, why)
    if not out.get("is_error"):
        ctx.restart_requested = True
    return out
