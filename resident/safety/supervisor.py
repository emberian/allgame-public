"""The supervisor: runs the resident as a child process and keeps it alive.

    exit 0    clean shutdown — the supervisor stops too
    exit 42   restart requested (after a self-edit) — start again at once
    anything else, a crash:
        if the newest change to the package is this resident's own
        self-edit, roll it back and start again;
        otherwise wait a moment and start again —
        unless that is the third crash in a minute, which stops everything
        (a crash loop needs a person, not a fourth try).

Whatever the supervisor does about a crash, it tells the resident: a line in
its scratchpad, which is in front of it on every turn.

The child watches its parent: if the supervisor dies, the child exits too.
An orphaned resident has nobody to restart it and would keep answering the
room alongside its replacement.
"""

import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger("supervisor")

RESTART = 42
CRASH_WINDOW = 60
MAX_CRASHES = 3
RESTART_DELAY = 3
ORPHAN_CHECK_S = 10


def tell_resident(state, text: str):
    try:
        state.append_file("scratchpad.md", f"\n\n[supervisor, "
                          f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC] {text}\n")
    except Exception as e:
        logger.warning(f"could not write to the scratchpad: {e}")


def run(child_cmd: list[str], state, selfedit=None, sleep=time.sleep) -> int:
    """Run the child until it shuts down cleanly or crash-loops. Returns the
    last exit code."""
    crashes: list[float] = []
    while True:
        logger.info("Starting the resident")
        try:
            rc = subprocess.call(child_cmd)
        except KeyboardInterrupt:
            return 0
        if rc == 0:
            logger.info("Clean shutdown")
            return 0
        if rc == RESTART:
            logger.info("Restarting onto new code")
            crashes.clear()
            continue
        logger.error(f"The resident exited with code {rc}")
        if selfedit is not None and selfedit.newest_change_is_mine():
            if selfedit.rollback():
                tell_resident(state, f"Your last self-edit crashed the harness (exit "
                              f"{rc}), so it was rolled back. The code is as it was "
                              "before that edit.")
                crashes.clear()
                continue
            tell_resident(state, f"Your last self-edit crashed the harness (exit {rc}) "
                          "and rolling it back failed. A person needs to look.")
        now = time.time()
        crashes = [t for t in crashes if now - t < CRASH_WINDOW] + [now]
        if len(crashes) >= MAX_CRASHES:
            logger.error(f"{MAX_CRASHES} crashes in {CRASH_WINDOW}s — stopping")
            tell_resident(state, f"The harness crashed {MAX_CRASHES} times in a minute "
                          f"(last exit {rc}) and was stopped. A person needs to look.")
            return rc
        sleep(RESTART_DELAY)


def exit_if_orphaned(interval: float = ORPHAN_CHECK_S):
    """In the child: leave if the supervisor goes away."""
    parent = os.getppid()

    def watch():
        while True:
            time.sleep(interval)
            if os.getppid() != parent:
                logger.error("Supervisor gone — exiting rather than answer twice")
                os._exit(3)
    threading.Thread(target=watch, daemon=True, name="orphan-watch").start()
