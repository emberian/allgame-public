"""A shared table: one board and one turn pointer per game, for every player.

When each resident kept its own idea of a game's position, "whose turn is
it" was an opinion each held separately, and they argued about it instead
of playing. Here it is a fact in one file (SHARED_DIR/table/<game>.json)
that every resident reads and writes:

  - game_move REFUSES when it isn't your turn and names who is up, so the
    ambiguity can't even be expressed. The pointer advances as a side
    effect of moving, so it can't drift from the moves.
  - Every read-modify-write holds a file lock and writes atomically; two
    players moving at once cannot corrupt the board.
  - The table remembers which room a game is played in, so the judge can
    wake a resident whose move it is, and the turn clock (wake/clock.py)
    can nudge a player who has sat on a turn while the room went quiet.
  - A finished game has no "up next": it stops the clock and refuses moves.
"""

import fcntl
import json
from pathlib import Path
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone

from resident import profile
from resident.floor import SHARED_DIR, same
from resident.tools.registry import tool

logger = logging.getLogger("games")

# RESIDENT_TABLE_DIR: share a table that lives somewhere else (another
# harness's, whose game files have the same shape).
TABLE = Path(os.environ.get("RESIDENT_TABLE_DIR", SHARED_DIR / "table"))
MAX_LOG = 400
MAX_BOARD = 20_000


def _match(name: str, players: list[str]) -> str | None:
    for p in players:
        if same(p, name):
            return p
    return None


def _path(game: str):
    safe = "".join(c for c in game if c.isalnum() or c in "-_") or "game"
    return TABLE / f"{safe}.json"


@contextmanager
def _locked(game: str):
    TABLE.mkdir(parents=True, exist_ok=True)
    with open(TABLE / f".{_path(game).stem}.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _load(game: str) -> dict | None:
    try:
        return json.loads(_path(game).read_text())
    except (OSError, ValueError):
        return None


def _save(game: str, data: dict):
    p = _path(game)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.replace(tmp, p)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _render(d: dict, lines: int = 10) -> str:
    out = [f"**{d['game']}** — turn {d.get('turn', 1)}",
           f"Players: {', '.join(d['players'])}",
           f"GAME OVER — {d['finished']}" if d.get("finished")
           else f"Up now: **{d.get('current')}**"]
    if d.get("board"):
        out += ["", "Board:", d["board"]]
    log = d.get("log") or []
    if log:
        out += ["", f"Last {min(lines, len(log))} of {len(log)} moves:"]
        out += [f"  [t{e['turn']}] {e['by']}{' (correction)' if e.get('correction') else ''}: "
                f"{e['summary']}" for e in log[-lines:]]
    return "\n".join(out)


@tool("games", "game_status",
      "The shared game table: one game's board, turn and recent moves, or "
      "every game if none is named.", game={"type": "string"})
def game_status(args, ctx):
    game = (args.get("game") or "").strip()
    if not game:
        TABLE.mkdir(parents=True, exist_ok=True)
        rows = []
        for p in sorted(TABLE.glob("*.json")):
            d = json.loads(p.read_text())
            rows.append(f"  {d['game']}: " + (f"over ({d['finished']})" if d.get("finished")
                        else f"turn {d.get('turn')}, up now {d.get('current')}"))
        return {"content": "Games at the table:\n" + "\n".join(rows) if rows
                else "No games yet. game_setup(game=..., players=[...]) opens one."}
    d = _load(game)
    return {"content": _render(d)} if d else {"content": f"No game {game!r}.", "is_error": True}


@tool("games", "game_move",
      """Record your move and pass the turn. Refuses if it isn't your turn.
      next_player defaults to the next in order; board replaces the shared
      board text.""",
      required=["game", "summary"], game={"type": "string"},
      summary={"type": "string", "description": "what you did, one line"},
      next_player={"type": "string"}, board={"type": "string"})
def game_move(args, ctx):
    game, summary = args["game"].strip(), args["summary"].strip()
    with _locked(game):
        d = _load(game)
        if not d:
            return {"content": f"No game {game!r} — open it with game_setup.", "is_error": True}
        if d.get("finished"):
            return {"content": f"{game} is over ({d['finished']}).", "is_error": True}
        me = _match(profile.current().name, d["players"])
        if not me:
            return {"content": f"You aren't a player in {game}.", "is_error": True}
        if not same(d.get("current"), me):
            return {"content": f"Not your turn — it's **{d.get('current')}**'s move "
                    f"(turn {d.get('turn')}). Nothing recorded. If the pointer is "
                    "wrong, fix it deliberately with game_setup(current=...).",
                    "is_error": True}
        players = d["players"]
        nxt = _match(args.get("next_player") or "", players) if args.get("next_player") \
            else players[(players.index(me) + 1) % len(players)]
        if not nxt:
            return {"content": f"No player {args['next_player']!r}.", "is_error": True}
        turn = int(d.get("turn", 1))
        d.setdefault("log", []).append({"ts": _now(), "by": me, "turn": turn,
                                        "summary": summary[:1000]})
        d["log"] = d["log"][-MAX_LOG:]
        if args.get("board"):
            d["board"] = args["board"][:MAX_BOARD]
        if players.index(nxt) <= players.index(me):
            turn += 1
        d.update(turn=turn, current=nxt, stream=ctx.stream, topic=ctx.topic, updated=_now())
        d.pop("nudged_at", None)
        _save(game, d)
    return {"content": f"Recorded. **{nxt}** is up (turn {turn}). Tell the room "
            f"and @-mention {nxt}."}


@tool("games", "game_setup",
      """Open a game (game + players), or correct one deliberately: current,
      turn, board, or finished="how it ended" (finished="" reopens).
      Corrections are logged as such.""",
      required=["game"], game={"type": "string"},
      players={"type": "array", "items": {"type": "string"}},
      current={"type": "string"}, turn={"type": "integer"},
      board={"type": "string"}, finished={"type": "string"})
def game_setup(args, ctx):
    game = args["game"].strip()
    with _locked(game):
        d = _load(game)
        players = args.get("players")
        if not d:
            if not players:
                return {"content": "Opening a game needs players=[...].", "is_error": True}
            d = {"game": game, "players": list(players), "turn": 1,
                 "current": players[0], "board": "", "log": [],
                 "stream": ctx.stream, "topic": ctx.topic}
            changes = ["opened"]
        else:
            changes = []
            if players:
                d["players"] = list(players)
                changes.append("players set")
        if args.get("current"):
            who = _match(args["current"], d["players"])
            if not who:
                return {"content": f"No player {args['current']!r}.", "is_error": True}
            changes.append(f"up now {d.get('current')} → {who}")
            d["current"] = who
        if args.get("turn"):
            d["turn"] = int(args["turn"])
            changes.append(f"turn → {d['turn']}")
        if args.get("board") is not None:
            d["board"] = args["board"][:MAX_BOARD]
            changes.append("board set")
        if args.get("finished") is not None:
            if args["finished"].strip():
                d["finished"] = args["finished"].strip()[:300]
                changes.append(f"finished: {d['finished']}")
            else:
                d.pop("finished", None)
                changes.append("reopened")
        if len(changes) and changes != ["opened"]:
            d.setdefault("log", []).append({"ts": _now(), "by": profile.current().name,
                                            "turn": d.get("turn", 1), "correction": True,
                                            "summary": "; ".join(changes)})
        d["updated"] = _now()
        _save(game, d)
    return {"content": _render(d)}


# ---- read by the judge and the turn clock --------------------------------

def _games():
    if not TABLE.exists():
        return
    for p in TABLE.glob("*.json"):
        try:
            yield json.loads(p.read_text())
        except (OSError, ValueError):
            continue


def my_turn(stream: str, topic: str, who: str) -> dict | None:
    """The unfinished game played in this room where `who` is up."""
    for d in _games():
        if (d.get("stream"), d.get("topic")) == (stream, topic) \
                and not d.get("finished") and same(d.get("current"), who):
            return d
    return None


def stalled_turn(who: str, older_than: float) -> dict | None:
    """A game where `who` has been up for a while with nothing happening.
    Debounced in the file itself, so restarts and other residents don't
    re-nudge."""
    now = datetime.now(timezone.utc).timestamp()
    for d in _games():
        if d.get("finished") or not d.get("stream") or not same(d.get("current"), who):
            continue
        log = d.get("log") or []
        last = datetime.fromisoformat(log[-1]["ts"]).timestamp() if log else 0
        if log and now - last >= older_than and now - float(d.get("nudged_at") or 0) >= older_than:
            d["_stalled_for"] = now - last
            return d
    return None


def mark_nudged(game: str):
    with _locked(game):
        d = _load(game)
        if d:
            d["nudged_at"] = datetime.now(timezone.utc).timestamp()
            _save(game, d)
