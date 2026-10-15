"""The supervisor, with a child that exits with scripted codes."""

import sys

from resident.safety import supervisor


def child(tmp_path, codes):
    """A child command that exits with codes[n] on its n-th run."""
    counter = tmp_path / "runs"
    counter.write_text("0")
    script = (f"import sys, pathlib\n"
              f"p = pathlib.Path({str(counter)!r})\n"
              f"n = int(p.read_text()); p.write_text(str(n + 1))\n"
              f"sys.exit({codes!r}[min(n, {len(codes) - 1})])\n")
    return [sys.executable, "-c", script], counter


class FakeSelfEdit:
    def __init__(self, mine=True, works=True):
        self.mine, self.works, self.rolled = mine, works, 0

    def newest_change_is_mine(self):
        return self.mine

    def rollback(self):
        self.rolled += 1
        self.mine = False
        return self.works


def test_restart_then_clean_exit(tmp_path, state):
    cmd, runs = child(tmp_path, [42, 0])
    assert supervisor.run(cmd, state, sleep=lambda s: None) == 0
    assert runs.read_text() == "2"


def test_crash_loop_stops_and_says_so(tmp_path, state):
    cmd, runs = child(tmp_path, [1])
    assert supervisor.run(cmd, state, sleep=lambda s: None) == 1
    assert runs.read_text() == str(supervisor.MAX_CRASHES)
    assert "crashed" in state.read_file("scratchpad.md")


def test_crash_after_own_self_edit_is_rolled_back(tmp_path, state):
    cmd, runs = child(tmp_path, [1, 0])
    se = FakeSelfEdit()
    assert supervisor.run(cmd, state, se, sleep=lambda s: None) == 0
    assert se.rolled == 1
    assert "rolled back" in state.read_file("scratchpad.md")


def test_failed_rollback_counts_toward_the_breaker(tmp_path, state):
    cmd, _ = child(tmp_path, [1])
    se = FakeSelfEdit(works=False)
    assert supervisor.run(cmd, state, se, sleep=lambda s: None) == 1
    assert "failed" in state.read_file("scratchpad.md")
