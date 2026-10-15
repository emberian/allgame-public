"""Self-editing against a throwaway git repo holding a tiny `resident` package."""

import subprocess

import pytest

from resident.safety.selfedit import SelfEdit


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          check=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "checkout"
    pkg = r / "resident"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "judge.py").write_text("LIMIT = 3\n")
    (pkg / "floor.py").write_text("from resident.judge import LIMIT\nTTL = LIMIT * 30\n")
    (r / "NOTES.md").write_text("a person's file\n")
    git(r, "init", "-q")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "test")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "init")
    return r


def subjects(repo):
    return git(repo, "log", "--format=%s").splitlines()


def test_edit_is_checked_and_committed_under_its_name(repo):
    out = SelfEdit(repo, "Juniper").edit(
        "judge.py", [{"old_string": "LIMIT = 3", "new_string": "LIMIT = 4"}], "raise the limit")
    assert not out.get("is_error"), out
    assert (repo / "resident/judge.py").read_text() == "LIMIT = 4\n"
    assert subjects(repo)[0] == "Juniper self-edit: raise the limit"


def test_unparseable_edit_changes_nothing(repo):
    out = SelfEdit(repo, "Juniper").edit(
        "judge.py", [{"old_string": "LIMIT = 3", "new_string": "LIMIT = ("}], "oops")
    assert out["is_error"] and "parse" in out["content"]
    assert (repo / "resident/judge.py").read_text() == "LIMIT = 3\n"
    assert subjects(repo) == ["init"]


def test_edit_that_breaks_an_import_is_rolled_back(repo):
    out = SelfEdit(repo, "Juniper").edit(
        "judge.py", [{"old_string": "LIMIT = 3", "new_string": "CAP = 3"}], "rename")
    assert out["is_error"] and "import" in out["content"]
    assert (repo / "resident/judge.py").read_text() == "LIMIT = 3\n"
    assert subjects(repo) == ["init"]


def test_cannot_reach_outside_the_package(repo):
    out = SelfEdit(repo, "Juniper").edit(
        "../NOTES.md", [{"old_string": "a", "new_string": "b"}], "sneaky")
    assert out["is_error"] and "outside" in out["content"]


def test_commits_only_its_own_file(repo):
    (repo / "NOTES.md").write_text("a person's unsaved work\n")         # dirty, unstaged
    (repo / "resident/floor.py").write_text(
        "from resident.judge import LIMIT\nTTL = LIMIT * 60\n")         # dirty, someone else's
    git(repo, "add", "resident/floor.py")                               # ...and staged
    SelfEdit(repo, "Juniper").edit(
        "judge.py", [{"old_string": "LIMIT = 3", "new_string": "LIMIT = 5"}], "bump")
    changed = git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert changed == ["resident/judge.py"]
    assert "unsaved" in (repo / "NOTES.md").read_text()
    assert "resident/floor.py" in git(repo, "diff", "--cached", "--name-only")


def test_rollback_restores_its_own_edit_only(repo):
    se = SelfEdit(repo, "Juniper")
    se.edit("judge.py", [{"old_string": "LIMIT = 3", "new_string": "LIMIT = 9"}], "nine")
    assert se.newest_change_is_mine()
    assert se.rollback()
    assert (repo / "resident/judge.py").read_text() == "LIMIT = 3\n"
    assert subjects(repo)[0].startswith("Juniper self-edit reverted")


def test_never_rolls_back_someone_elses_change(repo):
    SelfEdit(repo, "Juniper").edit(
        "judge.py", [{"old_string": "LIMIT = 3", "new_string": "LIMIT = 9"}], "nine")
    SelfEdit(repo, "Rowan").edit(
        "floor.py", [{"old_string": "* 30", "new_string": "* 20"}], "shorter")
    juniper = SelfEdit(repo, "Juniper")
    assert not juniper.newest_change_is_mine()
    assert not juniper.rollback()
    assert "* 20" in (repo / "resident/floor.py").read_text()


def test_create_and_delete(repo):
    se = SelfEdit(repo, "Juniper")
    assert not se.create("extra.py", "X = 1\n", "add extra").get("is_error")
    assert (repo / "resident/extra.py").exists()
    assert not se.delete("extra.py", "drop extra").get("is_error")
    assert not (repo / "resident/extra.py").exists()
    assert se.delete("judge.py", "drop judge")["is_error"]       # floor.py imports it
    assert (repo / "resident/judge.py").exists()
    assert se.delete("__init__.py", "no")["is_error"]


def test_rollback_of_a_create_removes_the_file(repo):
    se = SelfEdit(repo, "Juniper")
    se.create("extra.py", "X = 1\n", "add extra")
    assert se.rollback()
    assert not (repo / "resident/extra.py").exists()
