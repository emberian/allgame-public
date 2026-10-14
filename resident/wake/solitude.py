"""Solitude: a quiet turn of the resident's own when its rooms go still.

When a room it lives in has been idle for a while, the resident gets a turn
there with nobody waiting on it — to read, write in its journal, tidy its
notes, follow a thought, or say something unprompted if it wants to. At
most once every few hours per room. Silence is a perfectly good outcome.
"""

import logging
import threading
import time

from resident import profile

logger = logging.getLogger("solitude")

CHECK_S = 300

PROMPT = ("[SOLITUDE] This room has been quiet for a while, and this turn is "
          "yours — nobody is waiting on you. Read, write in your journal, tidy "
          "your notes, follow a thought, set yourself a timer, or say something "
          "if you genuinely want to. Doing nothing is fine too.")


def start(resident):
    last: dict[tuple, float] = {}

    def loop():
        w = profile.current().wake
        while True:
            time.sleep(CHECK_S)
            now = time.time()
            for stream in profile.current().chat.rooms:
                d = resident.state.root / "channels"
                logs = sorted((d / stream).glob("*.jsonl"), key=lambda f: f.stat().st_mtime) \
                    if (d / stream).exists() else []
                if not logs:
                    continue
                topic = __import__("json").loads(logs[-1].read_text().splitlines()[-1])["topic"]
                quiet = now - (resident.state.previous_message_time(stream, topic) or now)
                if quiet < w.solitude_quiet_minutes * 60:
                    continue
                if now - last.get((stream, topic), 0) < w.solitude_every_hours * 3600:
                    continue
                last[(stream, topic)] = now
                logger.info(f"Solitude turn in #{stream}>{topic}")
                try:
                    resident.run_turn(stream, topic, PROMPT)
                except Exception as e:
                    logger.warning(f"solitude turn failed: {e}")
    threading.Thread(target=loop, daemon=True, name="solitude").start()
