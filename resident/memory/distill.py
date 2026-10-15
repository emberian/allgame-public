"""The distiller: a room's older history, folded into a lasting memory.

Channel logs are kept forever, but a cold start only loads the newest part
of a room. What scrolls out of that window shouldn't simply vanish from the
resident's sense of the room, so a background thread folds the AGED part of
each log (everything but the newest RAW_KEEP messages) into a digest: who
was there and what they are like, running jokes, decisions, emotional
turns, phrases worth keeping. A cold start loads it as <room_memory>.

The digest is a reading of the logs, never a replacement. Its file
(channels/<stream>/<topic>.memory.json) keeps a cursor — how many log lines
are folded in — so each sweep folds one bounded chunk and moves on. Once
the rolling digest spans ERA_SPAN messages it is sealed as a volume and
never rewritten again; a new rolling digest begins.

When the provider refuses a chunk outright, the same chunk would be refused
on every later sweep, and the room's memory would stop for good. So the
distiller bisects: it finds the longest prefix the provider accepts (that
prefix's digest is kept), skips the one message just past it, and records
what it skipped. The log itself is never changed.
"""

import json
import logging
import threading
import time

from resident import llm, profile
from resident.util import safe_filename

logger = logging.getLogger("distill")

RAW_KEEP = 200            # the newest messages per room stay raw
MIN_BATCH = 100           # fold once this many have aged out
CHUNK_CHARS = 60_000      # one model call's worth of history
MEMORY_CHARS = 6_000      # the rolling digest's ceiling
ERA_SPAN = 2_000          # messages per sealed volume
LOAD_CHARS = 12_000       # how much a cold start loads
INTERVAL_S = 600

SYSTEM = """You keep the long memory of an AI resident of a group chat. You \
fold aged chat history into a digest: the residue a person keeps of a room's \
past after the exact words fade.

Keep, roughly in this order: who was there and what they are like; running \
jokes and recurring bits; decisions and commitments; emotional turns (fights, \
reconciliations, grief, delight); exact phrases worth keeping (quote them); \
dates when they anchor something. Compress older material harder.

Write compact lines a future reader can decode — names and concrete anchors, \
never pure abstraction. Output only the new digest, no preamble."""


def memory_path(log_rel: str) -> str:
    return log_rel[:-len(".jsonl")] + ".memory.json"


def _empty() -> dict:
    return {"through": 0, "head_from": 0, "memory": "", "eras": [], "skipped": []}


def _render(row: dict) -> str:
    return f"[{str(row.get('ts', ''))[:16]}] {row.get('sender', '?')}: {row.get('content', '')}"


def fold(previous: str, lines: list[str]) -> str:
    """One rewrite of the digest. Raises llm.PolicyRefusal if refused."""
    prompt = (f"<existing_digest>\n{previous}\n</existing_digest>\n\n"
              f"<aged_history>\n" + "\n".join(lines) + "\n</aged_history>\n\n"
              f"Rewrite the digest with the new history folded in. At most "
              f"{MEMORY_CHARS} characters.")
    return llm.complete(prompt, system=SYSTEM,
                        model=profile.current().models.memory).strip()[:MEMORY_CHARS]


def _save(state, rel: str, mem: dict, memory: str, through: int):
    if through - mem["head_from"] >= ERA_SPAN and memory:
        mem["eras"].append({"from": mem["head_from"], "to": through, "memory": memory})
        mem["head_from"], memory = through, ""
    mem.update(through=through, memory=memory, updated=time.time())
    state.write_file(rel, json.dumps(mem, ensure_ascii=False, indent=1))


def distill_log(state, log_rel: str) -> bool:
    """Fold one chunk of a log's aged history. True if anything was folded."""
    raw = state.read_file(log_rel) or ""
    lines = raw.splitlines()
    aged_end = len(lines) - RAW_KEEP
    rel = memory_path(log_rel)
    try:
        mem = {**_empty(), **json.loads(state.read_file(rel) or "{}")}
    except ValueError:
        mem = _empty()
    start = int(mem["through"])
    if aged_end - start < MIN_BATCH:
        return False

    chunk, end, used = [], start, 0      # chunk: (line index, msg id, text)
    for i in range(start, aged_end):
        try:
            row = json.loads(lines[i])
        except ValueError:
            end = i + 1
            continue
        text = _render(row)
        if chunk and used + len(text) > CHUNK_CHARS:
            break
        chunk.append((i, row.get("msg_id"), text))
        used, end = used + len(text), i + 1
    if not chunk:
        _save(state, rel, mem, mem["memory"], end)
        return False

    try:
        memory = fold(mem["memory"], [t for _, _, t in chunk])
    except llm.PolicyRefusal as refusal:
        return _step_over_refusal(state, rel, mem, chunk, log_rel, refusal)
    if not memory:
        return False
    _save(state, rel, mem, memory, end)
    logger.info(f"Distilled {log_rel}: lines {start}..{end}")
    return True


def _step_over_refusal(state, rel, mem, chunk, log_rel, refusal) -> bool:
    """Bisect for the longest accepted prefix; skip the message after it."""
    logger.warning(f"Refused while distilling {log_rel}; looking for the message")
    lo, hi, memory = 0, len(chunk), ""
    while hi - lo > 1:
        mid = (lo + hi) // 2
        try:
            memory, lo = fold(mem["memory"], [t for _, _, t in chunk[:mid]]), mid
        except llm.PolicyRefusal:
            hi = mid
    if lo == 0:
        try:
            fold(mem["memory"], [])
        except llm.PolicyRefusal:
            logger.error(f"The digest of {log_rel} is itself refused; it needs a "
                         f"person (delete or edit {rel})")
            return False
    idx, msg_id, text = chunk[lo]
    mem["skipped"].append({"line": idx, "msg_id": msg_id, "why": str(refusal)[:200]})
    logger.error(f"Skipped message {msg_id} in {log_rel}: the provider won't "
                 f"read it ({text[:80]!r})")
    _save(state, rel, mem, memory or mem["memory"], idx + 1)
    return bool(memory)


def room_memory(state, stream: str, topic: str, max_chars: int = LOAD_CHARS) -> str:
    """Sealed volumes and the rolling digest, newest kept when over budget."""
    raw = state.read_file(f"channels/{safe_filename(stream)}/"
                          f"{safe_filename(topic)}.memory.json")
    if not raw:
        return ""
    try:
        mem = json.loads(raw)
    except ValueError:
        return ""
    parts = [f"— Volume {i} (messages {e['from']}–{e['to']}):\n{e['memory']}"
             for i, e in enumerate(mem.get("eras", []), 1)]
    if mem.get("memory"):
        parts.append(f"— Recent, still settling:\n{mem['memory']}")
    while len(parts) > 1 and sum(map(len, parts)) > max_chars:
        parts.pop(0)
    return "\n\n".join(parts)[-max_chars:]


def sweep(state) -> int:
    channels = state.root / "channels"
    n = 0
    for log in sorted(channels.rglob("*.jsonl")) if channels.exists() else []:
        rel = log.relative_to(state.root).as_posix()
        try:
            n += distill_log(state, rel)
        except Exception as e:
            logger.warning(f"Distilling {rel} failed: {e}")
    return n


def start(state):
    def loop():
        while True:
            time.sleep(INTERVAL_S)
            n = sweep(state)
            if n:
                logger.info(f"Distiller: {n} room(s) folded")
    threading.Thread(target=loop, daemon=True, name="distiller").start()
