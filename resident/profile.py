"""A resident's profile: everything that differs from one resident to the next.

A profile is a directory holding `profile.toml` and the identity text it
points to. Two residents running this harness differ only here — name,
models, rooms, prompt, which tools they have, how they wake — never in code.

The profile is loaded once at startup and read through `current()`. It is a
process-wide setting on purpose: one process is one resident.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Models:
    main: str = "claude-opus-5-5"          # conversation turns
    fallback: str = "claude-sonnet-5-5"    # when the main model is unavailable
    judge: str = "claude-haiku-5-5"        # "should I speak?" — cheap and fast
    memory: str = "claude-opus-5-5"      # session notes


@dataclass
class Chat:
    backend: str = "zulip"                 # "zulip" or "console"
    zuliprc: str = "~/.zuliprc"
    # Rooms the resident lives in and may join unprompted.
    rooms: list[str] = field(default_factory=list)
    # Rooms where it speaks only when named.
    mention_only: list[str] = field(default_factory=list)
    # Other AI residents in these rooms. Two bots riffing with no human
    # steering can ping-pong a room forever; the judge is told who they are.
    peers: list[str] = field(default_factory=list)
    # Long-lived rooms ("stream>topic"): one conversation that persists
    # across days and restarts, born from the room's history and reborn when
    # it grows past limits.persistent_session_max_tokens.
    persistent: list[str] = field(default_factory=list)


@dataclass
class Wake:
    solitude: bool = True                  # quiet turns of its own
    solitude_quiet_minutes: int = 45       # a room must be idle this long first
    solitude_every_hours: int = 6          # and at most this often per room
    turn_stall_minutes: int = 8            # game turn clock


@dataclass
class Limits:
    # Longer messages are held as a draft and the resident chooses to trim
    # or send as is.
    message_soft_limit: int = 1000
    # A persistent room is reborn past this size, and born with this much
    # raw history. Every turn pays for the whole context, so these two set
    # the cost of a long-lived room.
    persistent_session_max_tokens: int = 500_000
    birth_raw_chars: int = 900_000


@dataclass
class Sandbox:
    image: str = "resident-sandbox"        # see docker/Dockerfile.sandbox
    network: str = "bridge"                # "none" for no network at all
    memory: str = "2g"
    cpus: float = 2.0
    timeout: int = 120                     # seconds, per call
    max_timeout: int = 600


# "sandbox" and "harness" (self-editing) are opt-in.
DEFAULT_TOOLS = ["state", "search", "web", "messaging", "games", "timers"]


@dataclass
class Profile:
    name: str
    dir: Path
    state_dir: Path
    identity: str                          # the prompt text that makes it someone
    models: Models = field(default_factory=Models)
    chat: Chat = field(default_factory=Chat)
    wake: Wake = field(default_factory=Wake)
    limits: Limits = field(default_factory=Limits)
    sandbox: Sandbox = field(default_factory=Sandbox)
    tools: list[str] = field(default_factory=lambda: list(DEFAULT_TOOLS))

    @property
    def bot_name(self) -> str:
        """Lowercased name, for matching mentions and own messages."""
        return self.name.lower()


def _section(cls, raw: dict | None):
    raw = raw or {}
    known = {f for f in cls.__dataclass_fields__}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown {cls.__name__.lower()} setting(s): "
                         f"{', '.join(sorted(unknown))}")
    return cls(**raw)


def load(path: str | Path) -> Profile:
    """Load `<dir>/profile.toml` (or a .toml path directly)."""
    path = Path(path).expanduser()
    toml = path if path.suffix == ".toml" else path / "profile.toml"
    data = tomllib.loads(toml.read_text())
    r = data.get("resident") or {}
    if not r.get("name"):
        raise ValueError(f"{toml}: [resident] name is required")
    identity_file = toml.parent / r.get("identity", "identity.md")
    p = Profile(
        name=r["name"],
        dir=toml.parent,
        state_dir=Path(r.get("state_dir", f"~/{r['name'].lower()}_state")).expanduser(),
        identity=identity_file.read_text() if identity_file.exists() else "",
        models=_section(Models, data.get("models")),
        chat=_section(Chat, data.get("chat")),
        wake=_section(Wake, data.get("wake")),
        limits=_section(Limits, data.get("limits")),
        sandbox=_section(Sandbox, data.get("sandbox")),
        tools=list((data.get("tools") or {}).get("enabled", DEFAULT_TOOLS)),
    )
    global _CURRENT
    _CURRENT = p
    return p


_CURRENT: Profile | None = None


def current() -> Profile:
    if _CURRENT is None:
        raise RuntimeError("no profile loaded — call resident.profile.load() first")
    return _CURRENT


def use(p: Profile) -> Profile:
    """Install a profile built in code (tests, embedding)."""
    global _CURRENT
    _CURRENT = p
    return p
