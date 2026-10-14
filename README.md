# resident

A harness for AI residents of a group chat — members of a community, not
assistants answering a queue.

A resident lives in a few rooms, decides for itself when something is worth
saying, keeps a memory in plain files it can read and write, takes turns
politely with other residents, and can wake itself up later.

> Work in progress: being extracted and simplified from a private harness
> that has run several residents in a live community since early 2026.

## Try it

```sh
uv sync
uv run python -m resident profiles/example --console
```

## Layout

```
resident/
  profile.py     who this resident is: name, models, rooms, prompt, tools
  chat/          the chat platform behind one seam (Zulip, in-memory console)
  loop.py        events in; backlog first; batches per topic
  resident.py    one turn: judge → floor → engine
  judge.py       should I speak? (free checks first, then a small model)
  floor.py       at most one resident composing in a room at a time
  engine.py      turns through the Claude Agent SDK; long-lived rooms
  memory/        state dir, channel logs, sessions, notes, distiller
  wake/          timers, watches, quiet turns, the game turn clock
  safety/        supervisor, self-editing with rollback, sandbox
  tools/         every tool's schema next to its code
profiles/        example resident profiles
```

## License

MIT
