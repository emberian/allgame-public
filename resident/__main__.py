"""python -m resident <profile-dir> [--console]

--console runs the resident against an in-memory chat in your terminal,
with no server: you type as a person in its first room.
"""

import argparse
import logging
import threading

from resident import profile
from resident.config import LOG_FORMAT


def main():
    ap = argparse.ArgumentParser(prog="python -m resident")
    ap.add_argument("profile", help="profile directory (holds profile.toml)")
    ap.add_argument("--console", action="store_true", help="local chat in this terminal")
    args = ap.parse_args()

    p = profile.load(args.profile)
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT,
                        filename=None if not args.console else str(p.state_dir / "resident.log"))
    p.state_dir.mkdir(parents=True, exist_ok=True)

    from resident.memory.state import StateManager
    from resident.resident import Resident
    state = StateManager(p.state_dir)

    if args.console:
        from resident.chat.console import MemoryChat, run_console
        chat = MemoryChat(p.name)
        room = (p.chat.rooms or ["lounge"])[0]
        if room not in p.chat.rooms:
            p.chat.rooms.append(room)
    else:
        from resident.chat.zulip import ZulipBackend
        chat = ZulipBackend(p.chat.zuliprc)

    resident = Resident(chat, state)
    from resident import wake
    from resident.memory import notes
    wake.start_all(resident)
    notes.start(resident)

    from resident import loop
    if args.console:
        threading.Thread(target=loop.run, args=(resident,), daemon=True).start()
        run_console(chat, room)
    else:
        loop.run(resident)


if __name__ == "__main__":
    main()
