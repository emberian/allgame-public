"""Harness constants: the same for every resident. Per-resident settings live
in the profile (resident/profile.py)."""

# Sessions in memory at once (their lifetime is the profile's
# engine.session_ttl_minutes).
SESSION_MAX_COUNT = 20

# Before a turn: a beat for the human to see their own message land, then
# wait (bounded) while someone is still typing, then sweep up messages that
# arrived meanwhile into the same turn.
COOLDOWN_SECONDS = 3
TYPING_HOLDOFF_TIMEOUT = 30
BURST_SWEEP_ROUNDS = 3
BURST_SWEEP_INTERVAL = 2.0

# Context sizes, in characters.
TOPIC_HISTORY_CHARS = 40_000        # a cold start's view of the room
STREAM_ACTIVITY_CHARS = 8_000       # other topics in the same stream
INTERIM_MESSAGES_CHARS = 8_000      # what was said between two turns
JOURNAL_TAIL_CHARS = 12_000

# One turn may call tools this many times.
MAX_TOOL_TURNS = 30

# On restart, missed messages older than this are history, not mail.
REPLAY_MAX_AGE_SECONDS = 3 * 86400
BACKFILL_PER_ROOM = 200

# The floor: a lease on "composing in this room", renewed while typing.
FLOOR_TTL = 90

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
