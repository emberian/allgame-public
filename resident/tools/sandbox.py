"""A sandbox: run code in a throwaway Docker container.

Each call is a fresh `docker run --rm` with memory, CPU, process and time
limits, a scratch workspace that persists across calls within one turn's
room (so a script written in one call can be run in the next), and the
resident's state directory mounted read-only at /state. Nothing in the
container can write to the host except the workspace.

Opt in by adding "sandbox" to a profile's tool groups and setting
[sandbox] image to an image you trust (anything with bash; see
`docker/Dockerfile.sandbox` for a small one with Python). If Docker is
missing or down, the tool says so and the resident carries on.
"""

import logging
import subprocess
import tempfile
from pathlib import Path

from resident import profile
from resident.tools.registry import tool

logger = logging.getLogger("sandbox")

OUTPUT_CHARS = 20_000
_WORKSPACES: dict[tuple[str, str], Path] = {}


def workspace(stream: str, topic: str) -> Path:
    key = (stream, topic)
    if key not in _WORKSPACES or not _WORKSPACES[key].exists():
        _WORKSPACES[key] = Path(tempfile.mkdtemp(prefix="resident_sandbox_"))
    return _WORKSPACES[key]


def docker_command(command: str, work: Path, state_dir: Path) -> list[str]:
    s = profile.current().sandbox
    return ["docker", "run", "--rm", "--network", s.network,
            "--memory", s.memory, "--cpus", str(s.cpus), "--pids-limit", "256",
            "-v", f"{work}:/workspace", "-v", f"{state_dir}:/state:ro",
            "-w", "/workspace", s.image, "/bin/bash", "-c", command]


@tool("sandbox", "run_sandbox",
      """Run a shell command in a throwaway Linux container (Docker). Files you
      pass are written to /workspace first; /workspace persists between calls
      in this room, and your state directory is readable at /state. Returns
      stdout, stderr, the exit code and the workspace's files.""",
      required=["command"],
      command={"type": "string", "description": "run with bash -c"},
      files={"type": "object", "description": "{filename: contents} to write first"},
      timeout={"type": "integer", "description": "seconds (default from the profile)"})
def run_sandbox(args, ctx):
    s = profile.current().sandbox
    work = workspace(ctx.stream, ctx.topic)
    for name, text in (args.get("files") or {}).items():
        target = (work / name).resolve()
        if not str(target).startswith(str(work.resolve())):
            return {"content": f"{name} is outside the workspace.", "is_error": True}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(text))
    timeout = max(5, min(int(args.get("timeout") or s.timeout), s.max_timeout))
    cmd = docker_command(args["command"], work, ctx.state.root)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"content": "Docker isn't installed here; the sandbox is unavailable.",
                "is_error": True}
    except subprocess.TimeoutExpired:
        return {"content": f"Timed out after {timeout}s.", "is_error": True}
    if r.returncode == 125 and "docker" in (r.stderr or "").lower():
        return {"content": f"The sandbox couldn't start: {r.stderr.strip()[:400]}",
                "is_error": True}
    out = r.stdout or ""
    if r.stderr:
        out += ("\n--- stderr ---\n" if out else "") + r.stderr
    out = (out or "(no output)")[-OUTPUT_CHARS:]
    files = sorted(f"  {p.relative_to(work)} ({p.stat().st_size} bytes)"
                   for p in work.rglob("*") if p.is_file())
    if files:
        out += "\n\n--- /workspace ---\n" + "\n".join(files)
    return {"content": out + f"\n\n[exit {r.returncode}]"}
