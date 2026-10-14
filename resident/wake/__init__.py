"""Turns nobody asked for: timers the resident set, the game turn clock, and
quiet turns of its own."""

import logging
import threading
import time

from resident import profile

logger = logging.getLogger("wake")

CLOCK_CHECK_S = 60


def start_all(resident):
    from resident.wake import solitude, timers
    timers.start(resident.state, resident.run_turn)
    if profile.current().wake.solitude:
        solitude.start(resident)
    threading.Thread(target=_clock, args=(resident,), daemon=True, name="clock").start()


def _clock(resident):
    """The game turn clock: if the table says it's this resident's move and
    nothing has happened for a while, take the turn — the room may have
    moved on, and nobody else is going to prompt it."""
    from resident.tools import games
    stall = profile.current().wake.turn_stall_minutes * 60
    while True:
        time.sleep(CLOCK_CHECK_S)
        try:
            g = games.stalled_turn(resident.name, stall)
            if not g:
                continue
            games.mark_nudged(g["game"])
            mins = int(g["_stalled_for"] // 60)
            logger.info(f"Turn clock: {g['game']} has waited on us {mins}m")
            resident.run_turn(g["stream"], g["topic"], (
                f"[HARNESS] The shared table says it's your move in {g['game']} "
                f"(turn {g.get('turn')}), and has been for {mins} minutes. Check "
                "game_status, make your move with game_move, and tell the room, "
                "@-mentioning whoever is up next. If you're blocked — waiting on "
                "dice, a ruling, a board nobody restated — say that instead, "
                "naming who you're waiting on. And if you'd rather sit this round "
                "out, say so or pass the turn; that keeps the game moving too."))
        except Exception as e:
            logger.warning(f"turn clock: {e}")
