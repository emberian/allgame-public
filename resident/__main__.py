"""python -m resident <profile-dir> [--console | --no-supervisor]

By default the resident runs under the supervisor (resident/safety): a
parent process that restarts it, rolls back a self-edit that crashed it,
and stops on a crash loop.

--console        an in-memory chat in your terminal, no server, no supervisor:
                 you type as a person in its first room
--no-supervisor  run the resident directly (under your own process manager)
"""

import argparse
import logging
import sys
import threading

from resident import profile
from resident.config import LOG_FORMAT


def main():
    ap = argparse.ArgumentParser(prog="python -m resident")
    ap.add_argument("profile", help="profile directory (holds profile.toml)")
    ap.add_argument("--console", action="store_true", help="local chat in this terminal")
    ap.add_argument("--no-supervisor", action="store_true", help="run without the supervisor")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    p = profile.load(args.profile)
    p.state_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT,
                        filename=str(p.state_dir / "resident.log") if args.console else None)

    from resident.memory.state import StateManager
    state = StateManager(p.state_dir)

    if not (args.console or args.no_supervisor or args.child):
        from resident.safety import supervisor
        selfedit = None
        if "harness" in p.tools:
            from resident.safety.selfedit import for_this_checkout
            selfedit = for_this_checkout(p.name)
        child = [sys.executable, "-m", "resident", args.profile, "--child"]
        sys.exit(supervisor.run(child, state, selfedit))

    if args.child:
        from resident.safety.supervisor import exit_if_orphaned
        exit_if_orphaned()

    from resident.resident import Resident
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
    from resident.memory import distill, notes
    wake.start_all(resident)
    notes.start(resident)
    distill.start(state)
    import importlib
    for name in p.modules:                  # a private package's own threads
        start = getattr(importlib.import_module(name), "start", None)
        if start:
            start(resident)

    from resident import loop
    if args.console:
        threading.Thread(target=loop.run, args=(resident,), daemon=True).start()
        run_console(chat, room)
    else:
        loop.run(resident)


if __name__ == "__main__":
    main()
