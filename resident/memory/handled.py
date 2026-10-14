"""Which messages the resident has already dealt with, across restarts.

"Was the resident the last speaker?" is a fragile test for "already
answered": a reply in another topic, a reaction, or a restart mid-turn all
fool it. This ledger is the durable answer instead. A message is marked once
a turn completes for it (answered, or deliberately skipped), so a restart
never answers it twice — and a turn that crashed is NOT marked, so it gets a
retry.

Append-only, one id per line: marking is a single append, so an interrupted
write cannot corrupt the set. Compacted to the newest `cap` ids on load.
"""

import logging
from collections import deque

logger = logging.getLogger("handled")

LEDGER_FILE = "handled_message_ids.log"


class HandledSet:
    def __init__(self, state, cap: int = 50_000):
        self.state = state
        self.cap = cap
        self._set: set[int] = set()
        self._order: deque[int] = deque()
        raw = state.read_file(LEDGER_FILE) or ""
        ids = [int(x) for x in raw.split() if x.strip().isdigit()]
        # A ledger that has never been written means first boot: there is no
        # downtime to bridge, so the loop treats history as read, not owed.
        self.pristine = not raw
        if len(ids) > cap:
            ids = ids[-cap:]
            state.write_file(LEDGER_FILE, "\n".join(map(str, ids)) + "\n")
        for i in ids:
            if i not in self._set:
                self._set.add(i)
                self._order.append(i)

    def __contains__(self, msg_id) -> bool:
        try:
            return int(msg_id) in self._set
        except (TypeError, ValueError):
            return False

    def mark(self, msg_id):
        try:
            mid = int(msg_id)
        except (TypeError, ValueError):
            return
        if mid in self._set:
            return
        self._set.add(mid)
        self._order.append(mid)
        self.state.append_file(LEDGER_FILE, f"{mid}\n")
        self.pristine = False
        while len(self._order) > self.cap:
            self._set.discard(self._order.popleft())
