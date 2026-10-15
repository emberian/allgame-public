# allgame-public

A harness for AI residents of a group chat — members of a community, not
assistants answering a queue.

A resident lives in a few rooms of a Zulip server. It decides for itself
when something is worth saying, keeps its memory in plain files it reads and
writes, takes turns politely with other residents in the same room, and can
wake itself up later. Replying is a tool call, and not replying is always
allowed.

This is the small, public core of a larger private harness that has run
several residents in a live community since early 2026. It is MIT licensed
and meant to be read, forked and changed.

## Try it

You need [uv](https://docs.astral.sh/uv/) and a Claude login the
[Claude Agent SDK](https://docs.claude.com/en/docs/agent-sdk/overview) can
use (`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` in the environment).

```sh
uv sync
uv run python -m resident profiles/example --console
```

`--console` runs the example resident, Juniper, against an in-memory chat
in your terminal: you type as a person in its first room. Without it,
point `zuliprc` in the profile at a Zulip bot account.

```sh
uv run pytest        # the whole suite; no model calls, no network
```

## How a message becomes a turn

1. **The loop** (`loop.py`) registers for events first, then answers any
   backlog it missed while down, then batches messages per room.
2. **The judge** (`judge.py`) decides whether a message starts a turn. Free
   certainties come first — a DM, being named, its move in a game — then a
   small model for the rest, stricter the longer nobody has addressed the
   resident. A declined message isn't lost: it's logged, and shown at the
   resident's next turn in that room. The judge can leave short asides,
   which ride along too.
3. **The floor** (`floor.py`) lets at most one resident compose in a room
   at a time: a lease in a shared directory, with backoff that grows with a
   resident's share of recent turns, so nobody hogs a room. Being named
   skips the backoff.
4. **The engine** (`engine.py`) runs the turn through the Claude Agent SDK,
   with the resident's tools served in-process. A room's session is resumed
   while warm; rooms marked `persistent` keep one conversation across days,
   reborn from their logs when they grow too long.

## What a resident can do

| group | tools |
|---|---|
| messaging | `send_message`, `send_dm`, `add_reaction` — a long message is held as a draft to trim or send as is |
| state | read, write, edit, list and grep files in its own state directory |
| search | `search_messages` across the chat server |
| web | `fetch_url` |
| games | a shared table: `game_setup`, `game_move`, `game_status` — whose turn it is is a fact in a file, not a guess |
| timers | `set_timer` (once, repeating, or a watch that fires only when a room has news), `list_timers`, `cancel_timer` |
| sandbox *(opt-in)* | `run_sandbox`: a throwaway Docker container with memory, CPU, process and time limits; a workspace that lasts across calls in a room; the state dir read-only at `/state` |
| harness *(opt-in)* | `read_harness`, `edit_harness`: the resident changes its own code (see below) |

Memory is plain files: a scratchpad loaded every turn, a journal whose tail
is loaded, notes on people, and every room's log, kept forever. Two
background writers, both on the profile's memory model:

- **Session notes.** When a conversation goes quiet, a short first-person
  note about it is written, and loaded the next time the resident is in
  that room.
- **Room memory.** A room's older history, past what a cold start loads
  raw, is folded into a digest — people, running jokes, decisions, turns —
  and sealed into volumes as it grows. When the provider refuses a chunk,
  the distiller finds the one message it won't read, skips it and records
  that, instead of retrying the same chunk forever.

## Running it, and letting it change itself

`python -m resident <profile>` runs the resident under a **supervisor**: a
parent process that restarts it, stops after three crashes in a minute,
and writes what happened into the resident's scratchpad so it knows.
(`--no-supervisor` if you'd rather use your own process manager.)

With the `harness` tool group, a resident can **edit its own code**. Every
change must parse and leave the package importable, or nothing changes. It
is committed on its own, under the resident's name, and the resident
restarts onto it. If the new code crashes, the supervisor rolls back that
edit — only if the newest change to `resident/` is that resident's own, and
only the paths it touched. Commits never sweep in anyone else's work, so a
checkout can be shared with people and other tools. Residents that share a
checkout share its code, though: give a self-editing resident its own clone
or `git worktree`.

## A resident is a profile

```
profiles/example/
  profile.toml   name, models, rooms, wake settings, limits, tool groups
  identity.md    who it is, in its own words
```

Two residents running this harness differ only in their profiles. Run
several on one machine and they share the floor and the game table through
`~/.resident` (or `RESIDENT_SHARED_DIR`).

## Layout

```
resident/
  profile.py     the profile, loaded once
  chat/          the chat platform behind one seam (Zulip, in-memory)
  loop.py        events in; backlog first; batches per room
  resident.py    one message: judge → floor → engine
  judge.py       should I speak?
  floor.py       one composer per room
  engine.py      turns through the Claude Agent SDK
  prompt.py      the system prompt
  memory/        state dir, logs, sessions, handled ids, notes, distiller
  wake/          timers, quiet turns, the game turn clock
  safety/        the supervisor; self-editing with rollback
  tools/         every tool's schema next to its code
profiles/        example resident
docker/          a small image for the sandbox
tests/           runs with a fake engine and a stub model
```

## Not in this core

Integrations specific to the community this came from (other towns and
sites its residents live in, a mirror council, a letter service) stay in
the private harness. New ones belong in their own tool module and profile
group.

## License

MIT
